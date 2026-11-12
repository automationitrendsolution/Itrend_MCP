# iTrend MCP Platform

One website to **run, share and watch MCP servers** at iTrend Solutions.

An MCP server gives an AI tool (Claude Desktop, VS Code, the PPC server…) safe access to
something, for example a database. This platform keeps all those servers in one place:

- **Run** them: start, stop and restart from the website.
- **Share** them: decide who can use which MCP, and give each person their own access token.
- **Watch** them: health, CPU, RAM, traffic and incidents, with automatic restarts.
- **Record** everything: every sign-in, connection and change is in the Audit Logs.

The live site will be at `https://mcp.itrendsolution.com`.

---

## Quick start

You need **[uv](https://docs.astral.sh/uv/)** (it installs Python 3.12 for you) and **MySQL 8**
with an empty database:

```sql
CREATE DATABASE itrend_mcp CHARACTER SET utf8mb4;
```

Then, in the project folder:

```powershell
uv sync                                       # 1. install everything
copy .env.example .env                        # 2. create your settings file
uv run python manage.py migrate               # 3. create the tables
uv run python manage.py createsuperuser       # 4. create your admin account
uv run python manage.py runserver             # 5. start the website
```

Open **http://127.0.0.1:8000** and sign in.

Before step 3, open `.env` and fill in:

| Setting | What to put |
|---|---|
| `DJANGO_SECRET_KEY` | A long random value. Make one with `uv run python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Your MySQL database and login |
| `MCP_INTROSPECTION_KEY` | Another random value (same command). MCP servers use it to check tokens |

> **No MySQL?** Set `DB_ENGINE=sqlite` for quick local work. It only works while
> `DJANGO_DEBUG=True`, so it can never reach production by accident.

---

## Daily use: two terminals

| Terminal | Command | What it does |
|---|---|---|
| 1 | `uv run python manage.py runserver` | The website |
| 2 | `uv run python manage.py monitor_mcps` | The monitor: checks every MCP, restarts crashed ones |

The monitor is optional, but without it the Monitoring page shows a warning and nothing is checked.

> **Keep the website on port 8000.** MCP servers check tokens at `http://127.0.0.1:8000`.
> If another project already uses port 8000, move that project, not this one.

---

## Who can do what

There are two separate things: **abilities** (what you can do on the platform) and
**MCP access** (which MCPs you can connect to).

**Abilities.** A superuser switches these on per person, on that person's user page:

| Ability | Lets you |
|---|---|
| Run MCP servers | Start, stop, restart and change MCP settings |
| Manage MCP access | Give and remove access to MCPs |
| See monitoring | View the Monitoring page (read only) |
| Manage users | Add, activate and deactivate people; revoke their tokens |
| See the audit log | View and download the Audit Logs |

**MCP access**, given per MCP, from lowest to highest:

| Level | Lets you |
|---|---|
| View | See the MCP, its connection link and its manual |
| Connect | View, plus create a token and connect an AI tool to it |
| Manage | Connect, plus edit the MCP's details |
| Admin | Manage, plus see its logs and give or remove other people's access |

Abilities do **not** give connect access. To connect, a person needs Connect (or higher)
on that MCP. Superusers can do everything.

**Service accounts** are for programs, not people (e.g. the PPC server). They cannot sign in,
and they get at most Connect.

---

## Connecting an AI tool to an MCP

1. Open **Access Tokens → Generate token**, pick the MCP and an expiry.
2. Copy the token. **It is shown only once.**
3. In your AI tool, use the MCP's link `http://127.0.0.1:8000/mcp/<name>/` with the header
   `Authorization: Bearer <your token>`. Each MCP's **Connect** tab shows ready-made examples.

Tokens stop working when they expire, are revoked, or you lose access to the MCP.
Tokens nobody has used for 30 days are flagged, with a one-click **Revoke unused**.

---

## Adding your own MCP

Create a folder under `mcp_servers/` with an `mcp.json` file in it. The platform finds it
by itself within about 30 seconds (or run `uv run python manage.py sync_mcps` to register it right away).

Step-by-step guide: [mcp_servers/README.md](mcp_servers/README.md).
Working example: [mcp_servers/mysql/](mcp_servers/mysql/).

---

## Useful commands

| Task | Command |
|---|---|
| Start the website | `uv run python manage.py runserver` |
| Start the monitor | `uv run python manage.py monitor_mcps` |
| Check every MCP once | `uv run python manage.py monitor_mcps --once` |
| Register new MCP folders now | `uv run python manage.py sync_mcps` |
| Run the tests | `uv run python manage.py test` |
| Apply database changes | `uv run python manage.py migrate` |
| Check the setup | `uv run python manage.py check` |
| Check production settings | `$env:DJANGO_DEBUG="False"; uv run python manage.py check --deploy` |
| Add a Python package | `uv add <package>` |

---

## Pages

| Page | Address | Who sees it |
|---|---|---|
| Overview | `/` | Everyone |
| MCP Registry | `/mcps/` | Everyone (only the MCPs they can see) |
| Access Tokens | `/tokens/` | Everyone |
| Monitoring | `/monitoring/` | "See monitoring" |
| Users | `/users/` | "Manage users" |
| Permissions | `/permissions/` | "Manage MCP access" |
| Audit Logs | `/activity/` | "See the audit log" (with CSV download) |
| Profile | `/accounts/profile/` | Everyone |
| Django admin | `/admin/` | Superusers |

## API

| Address | What it does |
|---|---|
| `/mcp/<name>/` | **The MCP gateway**: where AI tools connect. Needs a Bearer token |
| `/api/v1/mcps/` | List the MCPs you can see (read only) |
| `/api/v1/mcps/<id>/health/` | Latest health and last 24 hours of one MCP |
| `/api/v1/tokens/` | Your tokens: list, create (`POST`), revoke (`DELETE /api/v1/tokens/<id>/`) |
| `/api/v1/users/` | User list ("Manage users") |
| `/api/v1/health/` | Is the platform up? (public; 200 or 503) |
| `/api/v1/tokens/introspect/` | Token check for MCP servers only (needs `MCP_INTROSPECTION_KEY`) |

---

## Optional settings

All in `.env`; the defaults are fine for local work. See [.env.example](.env.example) for the full list.

| Setting | Default | What it changes |
|---|---|---|
| `TOKEN_LIFETIME_ALLOWED` | `all` | Who may create never-expiring tokens: `all`, `service_accounts` or `none` |
| `LOGIN_MAX_FAILED_ATTEMPTS` | `5` | Failed sign-ins before a short lockout |
| `LOGIN_LOCKOUT_SECONDS` | `900` | How long that lockout lasts |
| `SESSION_COOKIE_AGE` | `28800` | Seconds before you are signed out (8 hours) |
| `MONITORING_RETENTION_DAYS` | `14` | How long monitoring data is kept |
| `DJANGO_LOG_TO_FILE` | `False` | Also write logs to `logs/` |

---

## Project layout

```
config/        Settings and the main URL list
frontend/      The website: pages, forms, templates, CSS
backend/       Data, business rules (services/) and the REST API
mcp_servers/   The MCP servers, one folder each
tests/         Automated tests
```

Rule of thumb: what people **see** goes in `frontend/`; everything **behind** it goes in `backend/`.
