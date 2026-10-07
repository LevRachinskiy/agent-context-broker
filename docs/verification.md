# Verification record

Validation performed in the execution workspace on 2026-10-07 UTC.

| Check | Result |
| --- | --- |
| Full pytest run with actual Redis configured | 42 passed, 1 skipped; 93% application statement coverage |
| PostgreSQL integration test | Skipped; PostgreSQL requires a non-root service environment |
| MCP | Actual official SDK handshake/discovery and tool calls through a subprocess and live REST server passed |
| Lint and formatting | Ruff passed |
| Type checking | Mypy passed for all 27 application source files |
| Clean checkout | 41 tests passed, 2 optional service tests skipped; Ruff and Mypy passed |
| Locked dependency installation | `uv sync --frozen --extra dev` passed |
| Demo | Completed against live loopback HTTP server |
| Synthetic evaluation | Four cases completed; raw results committed |
| Load tests | 1,200 requests in each of local-cache and real-Redis profiles; no errors |
| Docker Compose | Configuration supplied; Docker unavailable in this workspace, so build/start unverified |
| GitHub Actions | Workflow published; inspect the latest run for current status |
| GitHub publication | Source published using the authenticated GitHub browser editor after integration content-write returned 403 |

The MCP subprocess is exercised by tests but its separate process is not included in the parent-process statement coverage report. Coverage is evidence of exercised branches, not a correctness guarantee.

Code review checked session scoping, database constraint/fingerprint behavior, bounded retrieval, UTF-8 budgeting, safe arithmetic, sanitized provider errors, timeout/retry policy, bounded metric labels, optional shared-key authentication, and secret exclusions. Public multi-tenant deployment still requires the controls described in the architecture document.

The workflow includes real PostgreSQL and Redis service tests plus Docker Compose build/start, demo, and evaluation. See the latest GitHub Actions run for the service and Docker results.
