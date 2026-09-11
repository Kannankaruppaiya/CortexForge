# CortexForge REST API Specification

## Base URL
`/api/v1`

## Endpoints Overview

### Authentication & Identity
- `GET /api/v1/auth/github`: Initiate unified GitHub OAuth 2.0 PKCE authorization flow.
  - Query params: `format: Optional[str]` ("json" returns `{ "authorization_url": str }`; omitted returns HTTP 307 redirect)
  - Security: Generates RFC 7636 PKCE `code_challenge` (S256) and stores single-use state in `oauth_transactions` table with 10-minute expiry.
- `GET /api/v1/auth/github/callback`: Process GitHub OAuth redirect and complete authentication.
  - Query params: `code: str`, `state: str`
  - Mechanism: Atomically burns state to prevent replay attacks, exchanges authorization code via server-side PKCE verification, retrieves user identity from GitHub (`/user` and verified primary email from `/user/emails`).
  - Account Logic: Unifies Sign Up and Sign In behind a single flow. Matches on immutable numeric `github_user_id`. Automatically provisions a new `User` record if new, or signs in and updates profile metadata (`github_login`, `avatar_url`) if existing.
  - Session: Creates a SHA-256 hashed session in the `sessions` table and issues HttpOnly, SameSite=Lax session cookies (`cortex_session` and `cortexforge_session`).
- `GET /api/v1/auth/me`: Get current authenticated user profile and active session details.
  - Authentication: Requires valid session cookie or `Authorization: Bearer <session_token>`.
  - Response: `200 OK` with `{ "id": UUID, "email": str, "github_user_id": str, "github_login": str, "avatar_url": str, "user": UserRead }`. Returns `401 Unauthorized` if unauthenticated.
- `POST /api/v1/auth/logout`: Terminate active session.
  - Revokes active database session and clears session cookies with expired `Max-Age=0`.

### Projects
- `POST /api/v1/projects`: Register a new project for cognitive tracking.
  - Body: `{ "name": str, "local_path": str, "repository_url": Optional[str], "default_branch": str }`
  - Response: `201 Created` with `ProjectRead`
- `GET /api/v1/projects`: List registered projects.
- `GET /api/v1/projects/{id}`: Get project details, last indexed commit, entity & memory counts.
- `DELETE /api/v1/projects/{id}`: Unregister project and purge memory/graph if requested.

### Scanner & Architecture
- `POST /api/v1/projects/{id}/scan`: Trigger a deterministic code scan (Tree-sitter AST & Git diff).
  - Body: `{ "incremental": bool = true, "max_files": Optional[int] }`
  - Response: `200 OK` with scan statistics (entities added/updated/removed, relationships discovered).
- `GET /api/v1/projects/{id}/architecture`: Retrieve high-level structural model of the project.
  - Query params: `depth: int = 2`, `module: Optional[str] = None`
  - Response: Modules, components, public APIs, database models, tests, and dependency graph.

### Graph
- `GET /api/v1/projects/{id}/graph`: Query the code entity graph.
  - Query params: `entity_type: Optional[str]`, `limit: int = 100`
- `GET /api/v1/projects/{id}/graph/dependencies`: Get dependencies for an entity.
  - Query params: `entity_id: UUID`, `depth: int = 3`
- `GET /api/v1/projects/{id}/graph/dependents`: Get dependents (upstream consumers) for an entity.
  - Query params: `entity_id: UUID`, `depth: int = 3`
- `GET /api/v1/projects/{id}/graph/impact`: Get affected entities and memories given a set of modified files.
  - Body: `{ "modified_files": list[str] }`

### Memories
- `GET /api/v1/projects/{id}/memories`: List project memories with multi-attribute filtering.
  - Query params: `type: Optional[str]`, `status: Optional[str]`, `min_importance: float`, `limit: int`, `offset: int`
- `POST /api/v1/projects/{id}/memories`: Create a durable project memory.
  - Body: `{ "memory_type": str, "title": str, "content": str, "summary": str, "importance": float, "source_type": str, "evidence": Optional[list[EvidenceCreate]] }`
- `GET /api/v1/memories/{id}`: Get full memory record including evidence, versions, and relations.
- `PATCH /api/v1/memories/{id}`: Update memory content or status with change reason.
- `POST /api/v1/memories/{id}/verify`: Trigger active verification of memory against current source code.
- `POST /api/v1/memories/{id}/deprecate`: Explicitly deprecate a memory with a superseding ID or rationale.

### Retrieval & Context
- `POST /api/v1/projects/{id}/retrieve`: Hybrid retrieval fusing lexical, semantic, graph, and provenance signals.
  - Body: `{ "query": str, "task_type": Optional[str], "limit": int = 10, "weights": Optional[RetrievalWeights] }`
- `POST /api/v1/projects/{id}/context`: Token-budget-aware structured context builder for coding agents.
  - Body: `{ "task_text": str, "profile": "small" | "medium" | "large", "max_tokens": int, "target_files": list[str] }`
  - Response: Structured context containing architecture, decisions, constraints, previous failures, and warnings.

### Agent Tasks & Events
- `POST /api/v1/projects/{id}/tasks`: Register an agent execution task.
- `GET /api/v1/projects/{id}/tasks/{task_id}`: Retrieve agent task details.
- `POST /api/v1/projects/{id}/events`: Record trajectory events (observations, tool calls, failures, decisions).

### Consolidation & Maintenance
- `POST /api/v1/projects/{id}/consolidate`: Run memory consolidation loop to merge duplicates, resolve contradictions, and archive stale episodic events.
- `GET /api/v1/projects/{id}/health`: Diagnostic report on memory health, stale rate, contradiction rate, and graph density.

### Evaluation & Benchmarks
- `GET /api/v1/evaluations`: List previous benchmark runs and scorecards.
- `POST /api/v1/evaluations/run`: Run evaluation benchmark suite comparing baseline vs CortexForge memory-assisted agent.
