"""
OAuth endpoints for MCP clients (see backend/services/oauth.py for the flow).

    GET  /.well-known/oauth-protected-resource/mcp/<slug>/   which server signs people in
    GET  /.well-known/oauth-authorization-server             what this server supports
    POST /oauth/register/                                    a client registers itself
    GET  /oauth/authorize/  (+ POST Allow / Deny)            sign-in and consent page
    POST /oauth/token/                                       code or refresh token -> tokens
"""

import base64
import binascii
import json
from urllib.parse import unquote, urlencode, urlsplit

from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from backend.models import MCPServer
from backend.services import access, oauth
from backend.services.login_protection import get_client_ip

REGISTRATIONS_PER_IP_PER_HOUR = 30
AUTHORIZE_FIELDS = ("client_id", "redirect_uri", "response_type", "state", "code_challenge", "code_challenge_method", "resource", "scope")


def origin(request: HttpRequest) -> str:
    return f"{request.scheme}://{request.get_host()}"


def _json(data: dict, status: int = 200) -> JsonResponse:
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    response["Access-Control-Allow-Origin"] = "*"  # public metadata; browser-based clients read it
    return response


def _error(error: oauth.OAuthError) -> JsonResponse:
    return _json(error.as_dict(), status=error.status)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@require_GET
def protected_resource_metadata(request: HttpRequest, slug: str) -> JsonResponse:
    server = MCPServer.objects.filter(slug=slug).first()
    if server is None or server.is_external or not server.internal_endpoint:
        raise Http404
    return _json(
        {
            "resource": oauth.resource_url(origin(request), server),
            "authorization_servers": [origin(request)],
            "scopes_supported": [oauth.SCOPE],
            "bearer_methods_supported": ["header"],
            "resource_name": server.name,
        }
    )


@require_GET
def authorization_server_metadata(request: HttpRequest) -> JsonResponse:
    base = origin(request)
    return _json(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize/",
            "token_endpoint": f"{base}/oauth/token/",
            "registration_endpoint": f"{base}/oauth/register/",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": list(oauth.AUTH_METHODS),
            "scopes_supported": [oauth.SCOPE],
        }
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@csrf_exempt
@require_POST
def register(request: HttpRequest) -> JsonResponse:
    key = f"oauth-register:{get_client_ip(request)}"
    cache.add(key, 0, 3600)
    if cache.incr(key) > REGISTRATIONS_PER_IP_PER_HOUR:
        return _error(oauth.OAuthError("slow_down", "Too many registrations; try again later.", status=429))
    try:
        data = json.loads(request.body or b"{}")
        client, secret, method = oauth.register_client(data)
    except ValueError:
        return _error(oauth.OAuthError("invalid_client_metadata", "The body must be JSON."))
    except oauth.OAuthError as error:
        return _error(error)
    answer = {
        "client_id": client.client_id,
        "client_id_issued_at": int(client.created_at.timestamp()),
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
        "scope": oauth.SCOPE,
    }
    if secret:
        answer.update(client_secret=secret, client_secret_expires_at=0)
    return _json(answer, status=201)


# ---------------------------------------------------------------------------
# Sign-in and consent
# ---------------------------------------------------------------------------


def _back_to_client(redirect_uri: str, **params) -> HttpResponseRedirect:
    joiner = "&" if "?" in redirect_uri else "?"
    clean = {key: value for key, value in params.items() if value}
    # A plain HttpResponseRedirect refuses non-http(s) schemes; ours are https or local http only.
    return HttpResponseRedirect(f"{redirect_uri}{joiner}{urlencode(clean)}")


@require_http_methods(["GET", "POST"])
def authorize(request: HttpRequest) -> HttpResponse:
    params = request.POST if request.method == "POST" else request.GET
    try:
        auth = oauth.check_authorize(params, request.get_host())
    except oauth.RedirectError as error:
        redirect_uri = params.get("redirect_uri", "")
        return _back_to_client(redirect_uri, error=error.code, error_description=error.description,
                               state=params.get("state", ""), iss=origin(request))
    except oauth.OAuthError as error:
        return render(request, "oauth/error.html", {"message": error.description}, status=400)

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())

    can_connect = access.can_connect(request.user, auth.server)
    if request.method == "GET":
        return render(
            request,
            "oauth/consent.html",
            {
                "auth": auth,
                "can_connect": can_connect,
                "fields": {name: params.get(name, "") for name in AUTHORIZE_FIELDS},
                "client_host": urlsplit(auth.redirect_uri).netloc,
            },
        )

    if request.POST.get("decision") != "allow" or not can_connect:
        return _back_to_client(auth.redirect_uri, error="access_denied", error_description="The person did not allow access.",
                               state=auth.state, iss=origin(request))
    try:
        code = oauth.issue_code(auth, request.user)
    except oauth.RedirectError as error:
        return _back_to_client(auth.redirect_uri, error=error.code, error_description=error.description,
                               state=auth.state, iss=origin(request))
    return _back_to_client(auth.redirect_uri, code=code, state=auth.state, iss=origin(request))


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def _client_credentials(request: HttpRequest) -> tuple[str, str]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(header[6:].strip()).decode()
        except (binascii.Error, UnicodeDecodeError):
            return "", ""
        client_id, _, secret = decoded.partition(":")
        return unquote(client_id), unquote(secret)
    return request.POST.get("client_id", ""), request.POST.get("client_secret", "")


@csrf_exempt
@require_POST
def token(request: HttpRequest) -> JsonResponse:
    try:
        client = oauth.authenticate_client(*_client_credentials(request))
        grant_type = request.POST.get("grant_type", "")
        host = request.get_host()
        resource = request.POST.get("resource", "")
        if grant_type == "authorization_code":
            answer = oauth.exchange_code(
                client,
                code=request.POST.get("code", ""),
                redirect_uri=request.POST.get("redirect_uri", ""),
                verifier=request.POST.get("code_verifier", ""),
                resource=resource,
                host=host,
            )
        elif grant_type == "refresh_token":
            answer = oauth.refresh(client, refresh_token=request.POST.get("refresh_token", ""), resource=resource, host=host)
        else:
            raise oauth.OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token.")
    except oauth.OAuthError as error:
        return _error(error)
    return _json(answer)
