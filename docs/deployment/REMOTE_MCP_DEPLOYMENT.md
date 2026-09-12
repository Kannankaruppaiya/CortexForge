# CortexForge: Remote MCP & Claude Custom Connector Deployment Guide

This guide details configuring and deploying CortexForge as a production remote Model Context Protocol (MCP) server accessible via Claude's Custom Connector interface (`Settings -> Connectors -> Add custom connector`).

---

## 1. Overview & Architecture

CortexForge supports two MCP transport modalities concurrently without compromising security, project isolation, or codebase indexing:

```
+-------------------------------------------------------------------------+
|                              Claude Clients                             |
+-------------------------------------------------------------------------+
       |                                                 |
  Local stdio                                       HTTPS (Remote)
  (Desktop / Cursor)                                (claude.ai / custom connector)
       |                                                 |
       v                                                 v
+------------------+                             +-------------------------------+
|  Local Stdio     |                             | Reverse Proxy / TLS (Nginx)   |
|  Entrypoint      |                             +-------------------------------+
|  (mcp_server.run)|                                     |
+------------------+                             +-------------------------------+
       |                                         | FastAPI Gateway               |
       |                                         | - /api/v1/... (REST)          |
       |                                         | - /health, /health/ready      |
       |                                         | - /mcp (Streamable HTTP)      |
       |                                         | - /.well-known/... (OAuth RS) |
       |                                         +-------------------------------+
       |                                                 |
       +--------------------+----------------------------+
                            |
                            v
       +-----------------------------------------+
       | Authentication & Principal Resolution   |
       | - OAuth 2.0 PKCE (RFC 7636 / RFC 6749)  |
       | - Direct Bearer Key (Agent / Session)   |
       | - Server-derived Principal & RBAC       |
       +-----------------------------------------+
                            |
                            v
       +-----------------------------------------+
       | CortexForge Cognition Engine & Storage  |
       | - 53 Discoverable Tools & Resources     |
       | - PostgreSQL + pgvector / SQLite        |
       +-----------------------------------------+
```

---

## 2. Remote HTTP MCP Setup (Claude Custom Connector)

### Endpoint URL Pattern
```
https://<your-production-domain>/mcp
```

### Steps to Connect from Claude
1. Open Claude (`claude.ai` or Claude Desktop with custom connector support).
2. Navigate to **Settings** -> **Connectors** (or **Integrations**).
3. Click **Add custom connector**.
4. Configure the connector:
   - **Name**: `CortexForge`
   - **MCP Server URL**: `https://<your-production-domain>/mcp`
5. Click **Continue**.
6. Claude will discover the protected resource metadata at `https://<your-production-domain>/.well-known/oauth-protected-resource/mcp` and perform OAuth 2.0 PKCE authentication with your server.
7. Upon successful authentication and consent, Claude will call `initialize` and discover all CortexForge tools (`project_get_context`, `project_get_architecture`, `memory_search`, `memory_create`, `graph_get_dependencies`, `task_record_decision`, etc.).

---

## 3. Reverse Proxy & TLS Configuration (Nginx / Caddy / Cloudflare)

Streamable HTTP MCP relies on Server-Sent Events (SSE) streaming (`text/event-stream`) and long-lived HTTP POST/GET connections. To prevent reverse proxy timeouts or buffer deadlocks, configure TLS termination as follows:

### Nginx Example
```nginx
server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/api.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.yourdomain.com/privkey.pem;

    # Forward to CortexForge FastAPI application
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        # Mandatory for Streamable HTTP / SSE streaming
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;

        # Standard proxy headers for host and IP resolution
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Extended timeouts for long-running reasoning / graph queries
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

---

## 4. Production Environment Variables

Configure the following variables in your `.env` or container environment:

| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `CORTEX_PUBLIC_URL` | Public HTTPS root URL of CortexForge | `https://api.yourdomain.com` |
| `CORTEX_ENV` | Runtime environment (`production`, `staging`, `development`) | `production` |
| `CORTEX_HOSTED` | Enforce hosted boundary (disables server local disk access) | `true` |
| `CORTEX_ALLOWED_ORIGINS` | Comma-separated list of allowed browser origins | `https://claude.ai,https://yourdomain.com` |
| `CORTEX_MCP_ALLOWED_HOSTS` | Comma-separated list of allowed host headers | `api.yourdomain.com` |
| `CORTEX_DB_URL` | Async SQLAlchemy database URL | `postgresql+asyncpg://user:pass@db:5432/cf` |
| `CORTEX_BRIDGE_URL` | Optional URL of the local workstation bridge | `https://bridge.yourdomain.com` |

> [!CAUTION]
> In `production` mode, `CORTEX_ALLOWED_ORIGINS` must never contain `*`. Credentialed remote requests combined with wildcard origins are strictly rejected.

---

## 5. Security Architecture & Scopes

### Least-Privilege MCP Scopes
CortexForge strictly prohibits wildcard `*` access. Remote agents must be granted specific, fine-grained scopes:

- `project:read` — Read project status, Git branch/commit metadata, and similar task histories.
- `context:read` — Generate token-budgeted prompt context packets for tasks.
- `code:read` — Query AST symbols, function signatures, and docstrings.
- `memory:read` — Search memories, decisions, failures, constraints, and causal provenance.
- `memory:write` — Propose candidate memories, update existing records, and submit for human approval.
- `graph:read` — Query relational symbol graph, upstream callers, and blast radius.
- `scan:trigger` — Request repository re-indexing and AST scanning.

### Hosted Filesystem Boundary
Hosted CortexForge instances running with `CORTEX_HOSTED=true` or in `production` will reject arbitrary server local paths (e.g. `C:\`, `/etc/passwd`). All repository code intelligence is accessed strictly through:
1. **GitHub Integration**: Cloned and indexed securely in isolated workspace directories.
2. **CortexForge Local Bridge**: Authenticated agent tunnel between developer workstations and the hosted memory layer.

---

## 6. Local Stdio MCP Setup

For developers using Claude Desktop, Cursor, or Codex locally on their machine, CortexForge preserves full stdio transport capabilities:

```json
{
  "mcpServers": {
    "cortexforge": {
      "command": "uv",
      "args": ["run", "python", "-m", "cortexforge.apps.mcp.server"],
      "cwd": "/path/to/your/project",
      "env": {
        "CORTEX_ENV": "development",
        "CORTEX_DB_URL": "sqlite+aiosqlite:///cortexforge.db"
      }
    }
  }
}
```

---

## 7. Health & Verification

- **Liveness probe**: `GET /health/live` — Returns 200 `{"status": "alive"}` if FastAPI process is alive.
- **Readiness probe**: `GET /health/ready` — Returns 200 only if the database connection is live and the MCP session manager task group is active. Returns 503 if degraded.
- **OAuth Metadata**: `GET /.well-known/oauth-authorization-server` — Returns RFC 8414 server metadata.
- **Resource Metadata**: `GET /.well-known/oauth-protected-resource/mcp` — Returns RFC 9728 resource metadata.
