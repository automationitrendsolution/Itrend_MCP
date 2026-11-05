from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsStaffOrReadOnly
from .serializers import MCPServerSerializer
from .services import registry
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


class MCPServerViewSet(viewsets.ModelViewSet):
    """
    /api/v1/mcps/

    Any logged-in user can list and view the MCP servers visible to them.
    Only administrators can create, update or delete.

    Query parameters for the list: ?search=, ?status=, ?deployment_type=
    """

    serializer_class = MCPServerSerializer
    permission_classes = [IsStaffOrReadOnly]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        params = self.request.query_params
        return registry.list_servers(
            self.request.user,
            search=params.get("search", ""),
            status=params.get("status", ""),
            deployment_type=params.get("deployment_type", ""),
        )

    def perform_create(self, serializer):
        serializer.instance = registry.create_server(created_by=self.request.user, **serializer.validated_data)

    def perform_update(self, serializer):
        registry.update_server(serializer.instance, updated_by=self.request.user, **serializer.validated_data)

    def perform_destroy(self, instance):
        registry.delete_server(instance, deleted_by=self.request.user)
