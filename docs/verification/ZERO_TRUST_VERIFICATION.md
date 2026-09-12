AGENTS.md

# CortexForge — Zero-Trust Engineering & Verification Policy

## 1. Core Rule

NEVER TRUST A PREVIOUS REPORT AS PROOF.

Statements such as:

- implemented
- fixed
- secure
- production ready
- all tests pass
- 100% complete
- 50/50 resolved

are claims, not evidence.

The current repository code, actual test execution, runtime behavior, and reproducible verification are the source of truth.

---

## 2. Mandatory Verification Model

Always distinguish:

IMPLEMENTED
TESTED
VERIFIED
UNVERIFIED

These are NOT interchangeable.

A feature being implemented does not mean it is verified.

A test existing does not mean the requirement is tested.

A test passing does not mean the security boundary is actually verified.

---

## 3. Verification Statuses

Use ONLY:

PASS
FAIL
UNVERIFIED
NOT APPLICABLE

### PASS

Only use PASS when:

- actual implementation was inspected
- relevant test was executed
- test exercises the real production code path
- security/correctness invariant was verified
- no known bypass remains

### FAIL

Use FAIL when:

- implementation violates the requirement
- vulnerability is reproducible
- expected behavior does not occur
- authorization/security boundary can be bypassed

### UNVERIFIED

Use UNVERIFIED when:

- required infrastructure is unavailable
- PostgreSQL was unavailable
- external OAuth credentials were unavailable
- required runtime environment was unavailable
- platform-specific behavior could not be tested
- test was skipped
- test was not actually executed

NEVER convert UNVERIFIED into PASS.

### NOT APPLICABLE

Use only when the requirement genuinely does not apply to the current architecture.

---

## 4. Never Self-Certify Without Evidence

Do NOT say:

"Fixed."

Instead verify:

1. Locate exact implementation.
2. Inspect the implementation.
3. Identify the security/correctness invariant.
4. Create or locate a test capable of falsifying the claim.
5. Execute the test.
6. Confirm the test actually executed.
7. Confirm the test exercises the real boundary.
8. Attempt the adversarial/failure path.
9. Record the evidence.
10. Only then assign PASS.

---

## 5. Security Claims Require Adversarial Testing

For every security feature, test both:

### Happy path

The authorized operation succeeds.

### Attack path

An unauthorized or malformed operation fails.

Examples:

- User A accessing User B's project
- invalid authentication
- expired session
- forged project owner
- forged agent identity
- forged epistemic authority
- arbitrary filesystem path
- path traversal
- symlink escape
- replayed OAuth state
- invalid OAuth state
- replayed approval token
- unauthorized MCP request
- malformed webhook
- oversized request
- invalid signature
- duplicate webhook
- race condition
- concurrent mutation

Security is not proven by happy-path tests.

---

## 6. Authentication != Authorization

Never assume authentication automatically provides authorization.

Authentication answers:

"Who is this principal?"

Authorization answers:

"Is this principal allowed to perform this operation on this resource?"

Both must be independently verified.

---

## 7. Identity Must Be Server-Derived

Never trust client-provided:

- user_id
- owner_user_id
- agent_id
- authority
- approver
- reviewer
- actor
- project owner

The server must derive the authenticated principal from a trusted authentication mechanism.

---

## 8. Project Isolation

CortexForge architecture:

User
  ↓
Projects
  ↓
AI Agents
  ↓
Project Data

There is currently NO tenant/organization layer.

Every project-scoped operation must enforce authorization.

Conceptually:

project_id
+
authenticated_user_id
+
authorization
=
authorized resource access

Never authorize solely because the caller knows:

project_id

Test for IDOR across:

- REST
- MCP
- background jobs
- snapshots
- memory
- graph
- scanner
- cognition
- retrieval
- verification
- benchmark
- economics
- webhooks
- internal services

---

## 9. Human Users and AI Agents

Human users and AI agents are distinct principals.

Do NOT treat:

agent_id
agent_name
agent metadata

as human authentication.

AI agents must have explicit project permissions.

An AI agent must not be able to self-assert elevated authority.

---

## 10. Epistemic Authority

Never trust request payloads such as:

authority=user_confirmed

or equivalent.

Epistemic authority must be established by trusted server-side workflows.

Client input must never promote a memory simply by claiming authority.

---

## 11. OAuth Verification

For GitHub OAuth verify the complete flow:

Continue with GitHub
→ OAuth start
→ state generation
→ PKCE generation
→ GitHub authorization
→ callback
→ state validation
→ PKCE validation
→ server-side code exchange
→ GitHub identity lookup
→ user lookup/creation
→ CortexForge session
→ authenticated dashboard

Verify:

- cryptographically random state
- state expiration
- state single-use
- PKCE S256
- server-side token exchange
- client secret never reaches browser
- stable GitHub user ID
- correct email handling
- existing user = sign in
- new user = sign up
- secure CortexForge session
- logout invalidates session

Never consider OAuth complete merely because the GitHub authorization page opens.

Never use:

mock_github_client_id

in a real authentication flow.

---

## 12. Sessions

Do not use GitHub access tokens as the CortexForge application session.

CortexForge must have its own session mechanism.

Verify:

- opaque/random session identifier
- server-side session validation
- expiration
- revocation
- session rotation after authentication
- HttpOnly
- Secure in production
- appropriate SameSite policy
- logout invalidation

Never place sensitive session credentials in URLs.

---

## 13. Database Verification

Never assume database correctness because:

- models compile
- SQLite tests pass
- create_all works

Production schema must be verified through the project's migration system.

For PostgreSQL-specific behavior, PostgreSQL must actually be available.

If PostgreSQL tests cannot run:

mark PostgreSQL-dependent requirements UNVERIFIED.

Never report:

"PostgreSQL verified"

when PostgreSQL was unavailable.

---

## 14. Migration Safety

Production schema changes must be migration-controlled.

Do not rely on:

Base.metadata.create_all()

as the production migration mechanism.

Verify:

- migration applies to clean database
- migration applies to existing database
- constraints exist
- indexes exist
- foreign keys exist
- nullability is correct
- rollback/downgrade behavior where supported

---

## 15. Filesystem Security

Any filesystem path supplied by a user, agent, MCP client, webhook, or API request is untrusted.

Verify:

- canonicalization
- workspace/project allowlist
- traversal protection
- symlink protection
- Windows path handling
- POSIX path handling
- UNC path handling where applicable
- relative path handling
- absolute path handling

Never authorize a filesystem path merely because:

os.path.exists(path)

returns true.

Never automatically register arbitrary filesystem paths as projects.

---

## 16. Incremental Scanner

Incremental scanning must preserve unrelated project data.

Verify:

- changed files are rescanned
- deleted files are handled
- deleted symbols are removed
- unchanged files remain intact
- unrelated relationships remain intact
- project isolation remains intact
- resource limits apply to changed files as well as full scans

Do not accept:

"incremental scanner implemented"

without proving these invariants.

---

## 17. Memory / Cognition Verification

Verify:

- project isolation
- claim/evidence relationship
- evidence provenance
- confidence computation
- confidence bounds
- verification behavior
- contradiction handling
- invalidation
- temporal history
- snapshot completeness
- deterministic replay where claimed
- graph relationship correctness
- ambiguous symbol resolution
- retrieval relevance

Do not claim "persistent cognition" merely because a database contains memories.

---

## 18. Snapshot / Replay

If the system claims historical replay or deterministic cognitive replay:

verify that the snapshot contains sufficient state to actually reproduce the historical result.

Metadata-only snapshots are NOT equivalent to complete cognitive state snapshots.

If exact replay is impossible:

mark the requirement UNVERIFIED or FAIL depending on the documented contract.

---

## 19. Durable Jobs

Verify:

- jobs survive API process restart
- workers actually run in production architecture
- actor identity survives enqueue/dequeue
- authorization is re-established by the worker
- retries are safe
- duplicate execution is handled
- idempotency exists where required
- failed jobs are observable

A function named:

run_loop()

does not prove a durable worker runtime exists.

---

## 20. MCP Security

For every MCP tool verify:

- authenticated principal
- project authorization
- agent authorization
- resource ownership
- no arbitrary filesystem registration
- no IDOR
- no self-asserted authority
- no secret leakage
- replay protection where applicable

Do not trust:

project_id

by itself.

---

## 21. Webhook Security

Verify:

- signature verification
- fail-closed behavior in production
- request body size limits
- immutable repository identity
- no unsafe name-based fallback
- idempotency
- replay resistance where appropriate
- concurrency/race handling

Never consider a webhook secure simply because HMAC verification exists.

---

## 22. API Security

Audit all routes.

For every endpoint determine:

- authentication required?
- authorization required?
- resource scope?
- input validation?
- maximum lengths?
- maximum counts?
- rate limiting?
- sensitive output?
- error leakage?

Do not assume internal endpoints are safe merely because they are not documented.

---

## 23. Frontend Security

Verify:

- authentication state comes from backend
- protected routes require authentication
- no secrets in frontend
- no client-controlled identity
- correct handling of 401/403
- OAuth callback behavior
- logout behavior
- project authorization errors

A successful frontend build does NOT prove authentication correctness.

---

## 24. Secrets

Never log or expose:

- OAuth client secret
- GitHub access token
- session token
- OAuth authorization code
- OAuth state
- PKCE verifier
- database password
- Redis credentials
- API keys

Search source code, logs, configuration, frontend bundles, tests, and documentation for accidental exposure.

---

## 25. Input Limits

All externally controlled input must have reasonable limits.

Audit:

- string lengths
- list sizes
- request body sizes
- webhook payload sizes
- file sizes
- scan limits
- recursion limits
- query limits
- pagination
- job payloads

Do not rely exclusively on application-level validation if infrastructure-level limits are required.

---

## 26. Git / Subprocess Security

Audit every subprocess invocation.

Verify:

- no unsafe shell interpolation
- argument boundaries
- repository path validation
- timeout
- resource limits
- environment sanitization
- output limits
- error handling

Never assume a subprocess is safe because it uses subprocess.run.

Inspect exactly how arguments are constructed.

---

## 27. Observability

Health checks must report actual system health.

Do not return:

database_connected=true

without actually verifying database connectivity.

Verify:

- DB health
- worker health
- queue health
- trace correlation
- metrics
- error visibility

Do not trust client-supplied trace IDs as authoritative security identity.

---

## 28. Tests Must Actually Execute

For every test suite record:

- total tests
- passed
- failed
- skipped
- xfailed
- environment limitations

Never report:

"all tests passed"

when tests were skipped.

Example:

54 passed, 6 skipped

is NOT equivalent to:

60 passed.

---

## 29. Test Causality

A passing test must genuinely test the requirement.

Do not accept tests that:

- mock away the security boundary
- never execute the relevant function
- only test helper functions
- assert implementation details unrelated to behavior
- use fake production paths
- silently skip on unavailable infrastructure

If a test can pass while the vulnerability remains, the test is insufficient.

---

## 30. Independent Red-Team Pass

After implementation, perform a separate hostile review.

Assume every previous fix may be wrong.

Ask:

"How can I bypass this?"

Try:

- forged IDs
- alternate routes
- direct database access through services
- MCP
- background jobs
- race conditions
- malformed input
- path traversal
- symlinks
- Windows paths
- replay
- stale sessions
- cross-user access
- client-controlled authority
- client-controlled actor
- webhook spoofing
- oversized requests

Do not stop after the first successful happy-path test.

---

## 31. Previous Reports Are Not Evidence

If a previous report states:

"All 30 blockers fixed"

verify all 30 independently.

If the current code contradicts the report:

TRUST THE CURRENT CODE.

Mark the claim FAIL.

If the environment prevents verification:

mark UNVERIFIED.

Never preserve a previous PASS simply because another agent reported it.

---

## 32. No Percentage-Based Confidence

Do not report:

98% complete
99% secure
50/50 fixed

unless the underlying methodology explicitly supports such a metric.

Prefer:

PASS: 42
FAIL: 3
UNVERIFIED: 5
NOT APPLICABLE: 0

with evidence for each.

---

## 33. Production-Ready Definition

Never declare:

"production ready"

unless:

- authentication verified
- authorization verified
- project isolation verified
- database migrations verified
- production database behavior verified
- filesystem security verified
- MCP security verified
- background jobs verified
- webhook security verified
- secrets verified
- frontend integration verified
- relevant tests executed
- critical adversarial tests passed
- no unresolved P0/P1 issue exists

If any critical requirement remains UNVERIFIED:

DO NOT call the system production ready.

---

## 34. Final Audit Report Format

For every requirement use:

Requirement:
<requirement>

Status:
PASS / FAIL / UNVERIFIED / NOT APPLICABLE

Evidence:
<exact file / function / route / test>

Test:
<exact test name or command>

Result:
<actual execution result>

Notes:
<limitations or findings>

For FAIL:

Severity:
P0 / P1 / P2 / P3

Reproduction:
<exact failure/attack path>

For UNVERIFIED:

Reason:
<exact infrastructure/environment limitation>

Required verification:
<what must be executed>

---

## 35. Final Rule

Never optimize the report for appearing complete.

Optimize for discovering what is actually broken.

A shorter report with:

PASS
FAIL
UNVERIFIED

and real evidence

is better than a confident report claiming:

"Everything is fixed."

The repository is the source of truth.
Actual execution is the evidence.
Adversarial verification is mandatory.