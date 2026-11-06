"""
Try the running MCP server with the official MCP client.

Generate a token for the MySQL MCP on the platform website (Access Tokens),
put it in the MCP_TOKEN environment variable, then run:

    $env:MCP_TOKEN = "itmcp_..."        # PowerShell
    uv run python -m mysql_mcp.demo_client
    uv run python -m mysql_mcp.demo_client "SELECT * FROM customers LIMIT 3"
"""

import json
import os
import sys

import anyio
from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from .config import PROJECT_DIR


async def run(sql: str | None) -> None:
    load_dotenv(PROJECT_DIR / ".env")
    url = os.getenv("MCP_PUBLIC_URL", f"http://127.0.0.1:{os.getenv('MCP_PORT', '8101')}/mcp")
    token = os.getenv("MCP_TOKEN", "").strip()
    if not token:
        sys.exit("Set MCP_TOKEN to a token generated on the platform website (Access Tokens → Generate token).")
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})

    async with http, Client(streamable_http_client(url, http_client=http)) as client:
        name = client.server_info.name if client.server_info else "MCP server"
        print(f"Connected to {name} at {url} (protocol {client.protocol_version})\n")
        tools = await client.list_tools()
        print("Tools:", ", ".join(tool.name for tool in tools.tools), "\n")

        result = await client.call_tool("run_query", {"sql": sql}) if sql else await client.call_tool("list_tables", {})
        if result.is_error:
            print("Error:", result.content[0].text)
        else:
            print(json.dumps(result.structured_content, indent=2, default=str))


if __name__ == "__main__":
    anyio.run(run, sys.argv[1] if len(sys.argv) > 1 else None)
