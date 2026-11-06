from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import AccessToken, MCPServer
from .permissions import can_manage_mcps
from .services import access
from .services.tokens import DEFAULT_EXPIRY_DAYS, EXPIRY_CHOICES_DAYS

# Internal details that only administrators may see.
STAFF_ONLY_FIELDS = ("internal_endpoint", "configuration")


class MCPServerSerializer(serializers.ModelSerializer):
    """Read-only: MCPs are added and changed through code (mcp_servers/<slug>/mcp.json)."""

    connection_url = serializers.CharField(read_only=True)
    created_by = serializers.CharField(source="created_by.get_username", read_only=True, default=None)

    class Meta:
        model = MCPServer
        fields = (
            "id", "name", "slug", "description", "deployment_type", "transport_type",
            "connection_url", "endpoint", "internal_endpoint", "configuration",
            "access_policy", "status", "health_status", "created_by", "created_at", "updated_at",
        )
        read_only_fields = fields

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is None or not can_manage_mcps(request.user):
            for name in STAFF_ONLY_FIELDS:
                fields.pop(name, None)
        return fields


class AccessTokenSerializer(serializers.ModelSerializer):
    """A token as listed. The secret value is never included."""

    mcp = serializers.CharField(source="mcp_server.slug", read_only=True)
    state = serializers.CharField(read_only=True)

    class Meta:
        model = AccessToken
        fields = (
            "id", "name", "mcp_server", "mcp", "token_prefix", "state",
            "created_at", "expires_at", "last_used_at", "revoked_at",
        )
        read_only_fields = fields


class AccessTokenCreateSerializer(serializers.Serializer):
    mcp_server = serializers.PrimaryKeyRelatedField(queryset=MCPServer.objects.all())
    name = serializers.CharField(max_length=100)
    expires_in_days = serializers.ChoiceField(choices=EXPIRY_CHOICES_DAYS, default=DEFAULT_EXPIRY_DAYS)

    def validate_mcp_server(self, server):
        # Unknown and hidden MCPs get the same answer, so their existence is not revealed.
        if not access.can_view(self.context["request"].user, server):
            raise serializers.ValidationError("MCP server not found.")
        return server


class UserSerializer(serializers.ModelSerializer):
    is_service_account = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = (
            "id", "username", "first_name", "last_name", "email",
            "is_active", "is_staff", "is_service_account", "last_login", "date_joined",
        )
        read_only_fields = fields

    def get_is_service_account(self, user) -> bool:
        return hasattr(user, "service_account")


class IntrospectionRequestSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200, trim_whitespace=True)
    mcp = serializers.SlugField(max_length=50)
