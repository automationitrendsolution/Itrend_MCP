# Your MCP servers

Every folder here is one MCP server. The platform finds them automatically,
lists them in the sidebar under **MCP Servers**, and lets administrators
**Start**, **Stop** and **Restart** them from the website.

```
mcp_servers/
  mysql/            ← folder name = the MCP's slug (used in its URL)
    mcp.json        ← tells the platform how to run it
    README.md       ← shown on the "User manual" tab
    .env            ← the MCP's own settings and secrets (never committed)
    pyproject.toml  ← its dependencies
    src/…
```

## Add a new MCP

1. **Create a folder** named after the MCP: 3–50 lowercase letters, numbers
   and hyphens, for example `mcp_servers/powerbi/`.
2. **Write the MCP** so that it:
   - serves **Streamable HTTP** (not stdio) on `127.0.0.1`;
   - reads its port from the `MCP_PORT` environment variable (the platform sets it);
   - answers `GET /health` with `200` (optional, but the Logs tab and status use it);
   - listens on `127.0.0.1` only. The platform's gateway checks every token
     before a request reaches your MCP, so token-checking code in the MCP is
     optional (the MySQL MCP checks again with `PlatformTokenVerifier`, as an
     extra safety layer). The platform also sets `MCP_SLUG` to the folder name.
3. **Add `mcp.json`:**

   ```json
   {
     "name": "Power BI MCP",
     "description": "Read reports and datasets from Power BI.",
     "command": ["uv", "run", "powerbi-mcp"],
     "port": 8102
   }
   ```

   | Key | Required | Meaning |
   |---|---|---|
   | `name` | yes | Shown on the website. Must be unique. |
   | `description` | no | One or two sentences. |
   | `command` | yes | How to start it, run inside this folder, as a list. |
   | `port` | yes | 1024–65535, different for every MCP. |
   | `mcp_path` | no | Default `/mcp`. |
   | `health_path` | no | Default `/health`; `null` = only check the port answers. |
   | `manual` | no | Default `README.md`; shown on the User manual tab. |

4. **Put its secrets in its own `.env`** (database passwords, API keys,
   `MCP_INTROSPECTION_KEY`). The platform never passes its own secrets to an MCP.
5. On the website, open **MCP Servers → All MCP servers** and click
   **Rescan MCP folders** (it also rescans by itself every 30 seconds), or run
   `uv run python manage.py sync_mcps`.
6. Open the MCP, give people access on **Who has access**, and click **Start**.

New MCPs start as *Restricted*: nobody except administrators can use them
until you give access.

## Change or remove an MCP

- **Name, description, command, port:** edit `mcp.json`, then rescan. Restart
  the MCP if it is running.
- **Remove:** stop it, delete the folder, then use **Remove from the platform**
  on its page. (While the folder exists, it would simply come back.)

## Good to know

- Output goes to `var/mcp_logs/<slug>.log` and is shown on the **Logs** tab.
- MCPs keep running when the website restarts.
- If a copy is already running in a terminal on the same port, the page shows
  *Running (started outside the platform)*. Stop that copy (Ctrl+C) to manage
  it from the website.
- People connect through the platform's gateway: `http://<platform>/mcp/<slug>/`
  (shown as the *Connection link* on the MCP's page). The gateway checks their
  token and permission, logs the connection and forwards the request to your
  MCP. Your MCP receives `X-ITrend-User` (who is calling) and
  `X-ITrend-Permission` headers. Keep your MCP on `127.0.0.1` so nobody can
  skip the gateway.
