import hmac
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MCPServer
from .permissions import CanManageUsers, IsAdministrator, MCPServerPermission, can_manage_mcps
from .serializers import (
    AccessTokenCreateSerializer,
    AccessTokenSerializer,
    IntrospectionRequestSerializer,
    MCPServerSerializer,
    UserSerializer,
)
from .services import gateway, monitoring, registry, tokens, traffic
from .services.health import check_platform


class HealthView(APIView):
    """
    GET /api/v1/health/

    Public, unauthenticated health check for load balancers and uptime
    monitors. It only reports up/down status and never exposes configuration.
    Returns HTTP 200 when healthy and 503 when a dependency is down.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        health = check_platform()
        http_status = status.HTTP_200_OK if health.is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(
            {"status": health.status, "database": health.database.status},
            status=http_status,
        )


class MCPServerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/v1/mcps/

    Any logged-in user can list and view the MCP servers visible to them.
    MCPs are added and changed through code (mcp_servers/<slug>/mcp.json), so
    this API is read-only.

    Query parameters for the list: ?search=, ?status=, ?deployment_type=
    """

    serializer_class = MCPServerSerializer
    permission_classes = [MCPServerPermission]

    def get_queryset(self):
        params = self.request.query_params
        return registry.list_servers(
            self.request.user,
            search=params.get("search", ""),
            status=params.get("status", ""),
            deployment_type=params.get("deployment_type", ""),
        )


class AccessTokenViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """
    /api/v1/tokens/

    GET     list your own tokens (the secret value is never returned)
    POST    create a token: {"mcp_server": <id>, "name": "...", "expires_in_days": 90}
            The response contains "token" once. Store it safely; it cannot be shown again.
    DELETE  /api/v1/tokens/<id>/ revokes the token (it stays in the list as "revoked").
    """

    serializer_class = AccessTokenSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return tokens.list_tokens(user=self.request.user)

    def create(self, request: Request) -> Response:
        serializer = AccessTokenCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        token, plaintext = tokens.create_token(
            owner=request.user,
            server=data["mcp_server"],
            name=data["name"],
            expires_in_days=data["expires_in_days"],
            created_by=request.user,
        )
        body = {**AccessTokenSerializer(token).data, "token": plaintext}
        return Response(body, status=status.HTTP_201_CREATED, headers={"Cache-Control": "no-store"})

    def perform_destroy(self, instance):
        tokens.revoke_token(instance, revoked_by=self.request.user)


class UserListView(ListAPIView):
    """GET /api/v1/users/  People with "Manage users". ?search= filters by name or email."""

    serializer_class = UserSerializer
    permission_classes = [CanManageUsers]

    def get_queryset(self):
        from .services.accounts import list_users

        return list_users(search=self.request.query_params.get("search", ""))


class TokenIntrospectionView(APIView):
    """
    POST /api/v1/tokens/introspect/   (for MCP servers, not for people)

    An MCP server sends the bearer token it received and its own slug:
        {"token": "itmcp_...", "mcp": "mysql"}
    and authenticates itself with "Authorization: Bearer <MCP_INTROSPECTION_KEY>".

    The answer is {"active": true, ...} only if the token may be used on that MCP
    right now; otherwise {"active": false}, without saying why (the reason is
    written to the activity log instead). Modelled on OAuth token introspection
    (RFC 7662).
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    # Called on every MCP request, by trusted servers holding the key.
    throttle_classes: list = []

    def post(self, request: Request) -> Response:
        self._check_caller(request)
        serializer = IntrospectionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = tokens.introspect(serializer.validated_data["token"], serializer.validated_data["mcp"])
        if not result.active:
            return Response({"active": False}, headers={"Cache-Control": "no-store"})
        token = result.token
        return Response(
            {
                "active": True,
                "mcp": token.mcp_server.slug,
                "username": token.user.get_username(),
                "token_id": token.pk,
                "permission": result.level,
                "expires_at": int(token.expires_at.timestamp()) if token.expires_at else None,
            },
            headers={"Cache-Control": "no-store"},
        )

    @staticmethod
    def _check_caller(request: Request) -> None:
        key = settings.MCP_INTROSPECTION_KEY
        if not key:
            raise PermissionDenied("Token introspection is not configured on this platform.")
        scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not supplied:
            raise NotAuthenticated("Send the introspection key as a bearer token.")
        if not hmac.compare_digest(supplied.encode(), key.encode()):
            raise PermissionDenied("Invalid introspection key.")


# ---------------------------------------------------------------------------
# MCP gateway: /mcp/<slug>/  (see backend/services/gateway.py)
# ---------------------------------------------------------------------------


def _gateway_error(error: gateway.GatewayError) -> JsonResponse:
    response = JsonResponse({"error": {"code": error.code, "message": error.message}}, status=error.status)
    if error.status == 401:
        response["WWW-Authenticate"] = f'Bearer realm="iTrend MCP", error="{error.code}"'
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt  # MCP clients authenticate with a bearer token, not a session cookie
def mcp_gateway(request: HttpRequest, slug: str, rest: str | None = None) -> HttpResponseBase:
    """Check the caller's token, then pass the request to the MCP and stream its answer back."""
    started = time.perf_counter()
    server = None
    try:
        server = gateway.find_server(slug)
        identity = gateway.authorize(server, request.headers.get("Authorization", ""))
        # Phase 6: per-MCP limits (backend/services/traffic.py).
        limits = traffic.limits_for(server)
        slot = traffic.acquire(server, limits)
        try:
            upstream = gateway.forward(
                server,
                method=request.method,
                rest=rest or "",
                query=request.META.get("QUERY_STRING", ""),
                headers={name.lower(): value for name, value in request.headers.items()},
                body=request.body,
                identity=identity,
                scheme=request.scheme,
                timeout=limits.request_timeout_seconds,
            )
        except BaseException:
            slot.release()
            raise
    except gateway.GatewayError as error:
        if server is not None:  # unknown slugs are not counted
            traffic.record(server, error.status, (time.perf_counter() - started) * 1000)
        return _gateway_error(error)

    # Response time = until the MCP starts answering (a stream may stay open much longer).
    traffic.record(server, upstream.status, (time.perf_counter() - started) * 1000)
    response = StreamingHttpResponse(traffic.ReleasingStream(upstream.body, slot), status=upstream.status)
    for name, value in upstream.headers.items():
        response[name] = value
    response["X-Accel-Buffering"] = "no"  # let Server-Sent Events through Nginx immediately
    return response


# ---------------------------------------------------------------------------
# Monitoring (Phase 6)
# ---------------------------------------------------------------------------


class MCPHealthView(APIView):
    """
    GET /api/v1/mcps/<id>/health/

    The latest real health check of an MCP you can see, plus its last 24 hours:
    uptime, automatic restarts and gateway traffic. CPU/RAM and the error text
    of the last check are shown to administrators only. MCPs you cannot see
    answer 404, the same as MCPs that do not exist.
    """

    def get(self, request: Request, pk: int) -> Response:
        server =get_object_or_404(MCPServer.objects.visible_to(request.user), pk=pk)
        data = monitoring.summary(server)
        last = data["last"]
        is_admin = can_manage_mcps(request.user)
        check = None
        if last is not None:
            check = {
                "status": last.status,
                "checked_at": last.checked_at,
                "response_time_ms": last.response_time_ms,
            }
            if is_admin:
                check.update(error=last.error_message, cpu_percent=last.cpu_percent, memory_mb=last.memory_mb)
        return Response(
            {
                "id": server.pk,
                "slug": server.slug,
                "status": server.status,
                "monitored": server.is_local,
                "last_check": check,
                "last_24h": {
                    "checks": data["checks"],
                    "uptime_percent": data["uptime_percent"],
                    "auto_restarts": data["auto_restarts"],
                    **data["traffic"],
                },
            }
        )
