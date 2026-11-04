from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .health import check_platform


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
