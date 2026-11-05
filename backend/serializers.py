from rest_framework import serializers

from .models import MCPServer
from .permissions import can_manage_mcps
from .services.registry import IMMUTABLE_FIELDS

# Internal deployment details that only administrators may see.
STAFF_ONLY_FIELDS = ("internal_endpoint", "configuration")


class MCPServerSerializer(serializers.ModelSerializer):
    connection_url = serializers.CharField(read_only=True)
    created_by = serializers.CharField(source="created_by.get_username", read_only=True, default=None)

    class Meta:
        model = MCPServer
        fields = (
            "id", "name", "slug", "description", "deployment_type", "transport_type",
            "connection_url", "endpoint", "internal_endpoint", "configuration",
            "access_policy", "status", "health_status", "created_by", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is None or not can_manage_mcps(request.user):
            for name in STAFF_ONLY_FIELDS:
                fields.pop(name, None)
        return fields

    def validate(self, attrs):
        if self.instance is not None:
            changed = {name for name in IMMUTABLE_FIELDS if name in attrs and attrs[name] != getattr(self.instance, name)}
            if changed:
                raise serializers.ValidationError({name: "This field cannot be changed after creation." for name in changed})
        return attrs
