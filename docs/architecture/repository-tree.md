# CortexForge Monorepo Repository Structure

```
cortexforge/
├── apps/
│   ├── api/                     # FastAPI application, route controllers, middleware
│   │   ├── routes/              # REST endpoint routers (projects, memories, graph, context)
│   │   ├── main.py              # Application entrypoint & lifespan
│   │   └── config.py            # API server configuration
│   ├── web/                     # Developer Web Dashboard (Next.js 15, React Flow, Tailwind)
│   ├── worker/                  # Background task worker (indexing, verification, consolidation)
│   └── mcp/                     # Model Context Protocol stdio & SSE server
│       ├── server.py            # MCP protocol entrypoint
│       └── tools.py             # Tool registrations mapping to core domain services
├── packages/
│   ├── core/                    # Domain models, database session management, base types
│   │   ├── models/              # SQLAlchemy ORM models (Project, CodeEntity, Memory, etc.)
│   │   ├── schemas/             # Pydantic v2 schemas for API contracts
│   │   └── db.py                # Database connection, migrations, and session factories
│   ├── code-intelligence/       # Tree-sitter parsers, AST extraction, and language analyzers
│   │   ├── parser.py            # Language-agnostic code intelligence interface
│   │   ├── treesitter/          # Tree-sitter implementations for TS, JS, Python, Java, Go
│   │   └── scanner.py           # Incremental filesystem & directory scanner
│   ├── graph/                   # Code & Cognitive knowledge graph engine
│   │   ├── service.py           # Dependency resolution and recursive CTE traversal
│   │   └── queries.py           # Graph query builders
│   ├── memory/                  # Layered memory engine (L0-L6), verification, consolidation
│   │   ├── service.py           # Memory CRUD, status transitions, versioning
│   │   ├── verification.py      # Automated code/test evidence verification engine
│   │   └── consolidation.py     # Memory consolidation, deduplication, and archival
│   ├── retrieval/               # Multi-signal hybrid retrieval engine
│   │   ├── engine.py            # Fusion of vector, lexical, graph, recency, provenance
│   │   └── composer.py          # Token-budget context builder
│   ├── llm/                     # Provider-neutral LLM client abstraction
│   │   └── provider.py          # OpenAI, Anthropic, Gemini, Ollama adapters
│   ├── embeddings/              # Versioned embedding generation interface
│   │   └── provider.py          # OpenAI, fastembed, sentence-transformers adapters
│   ├── evaluation/              # Benchmark harness and comparative scorecards
│   │   └── runner.py            # Automated evaluation runner for hypotheses H1-H5
│   ├── observability/           # OpenTelemetry tracing, metrics, and structured logging
│   └── security/                # Prompt injection defense, path canonicalization, trust levels
├── libs/
│   └── shared-types/            # Common domain types and enums
├── infra/
│   ├── docker/                  # Dockerfiles and docker-compose.yml
│   ├── postgres/                # PostgreSQL init scripts and pgvector setup
│   ├── otel/                    # OpenTelemetry collector configuration
│   └── github/                  # GitHub App manifests and webhook definitions
├── docs/                        # Complete architectural and research documentation
├── benchmarks/                  # Benchmark test suites and ground-truth tasks
└── tests/                       # Unit, integration, contract, and end-to-end tests
    ├── unit/
    ├── integration/
    └── e2e/
```
