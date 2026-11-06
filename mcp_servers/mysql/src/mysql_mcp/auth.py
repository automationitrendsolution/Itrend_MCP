"""
Bearer-token check for incoming MCP requests.

Phase 3 uses one shared key from MCP_API_KEY. Phase 4 replaces this verifier
with one that checks personal tokens issued by the iTrend MCP Platform; the
rest of the server stays the same.
"""

import hmac

from mcp.server.auth.provider import AccessToken

READ_SCOPE = "mysql:read"


class StaticKeyVerifier:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        # Constant-time comparison so response timing reveals nothing about the key.
        if hmac.compare_digest(token.encode(), self._api_key):
            return AccessToken(token=token, client_id="static-key", scopes=[READ_SCOPE])
        return None
