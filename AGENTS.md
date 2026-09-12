## Zero-Trust Verification Protocol

Before performing any security audit, production-readiness audit,
or verification task:

1. Read this AGENTS.md completely.
2. Then read:
   docs/verification/ZERO_TRUST_VERIFICATION.md
3. Treat both files as mandatory instructions.
4. Do not start the audit until both files have been read.
5. Follow ZERO_TRUST_VERIFICATION.md for the detailed verification methodology.
6. AGENTS.md has higher priority for project-level engineering rules.
7. If the two files conflict, follow AGENTS.md and explicitly report the conflict.

NEVER ACCEPT YOUR OWN PREVIOUS REPORT AS EVIDENCE.

Implemented != Tested != Verified != Production Ready.

Never convert SKIPPED into PASS.

Never convert UNVERIFIED into PASS.

Never trust:
- previous reports
- previous audit results
- test counts
- documentation claims
- comments claiming security
- "production ready" claims

The current repository code and actual execution are the source of truth.

Before declaring a security or production-readiness requirement PASS,
the requirement must have actual implementation evidence and executed
verification evidence.

If the current code contradicts a previous report, trust the current
code and mark the previous claim FAIL.

Never fabricate test results.

Never claim PostgreSQL verification if PostgreSQL was unavailable.

Never claim real GitHub OAuth verification if real GitHub OAuth was
not actually tested.

Never declare the system production ready while any production-critical
requirement remains FAIL or UNVERIFIED.