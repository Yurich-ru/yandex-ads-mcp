# 2026-11-06 19:00 — Shared OAuth plan revisions

Resolved the three P2 findings in the plan. Discovery validates and retains its own account filter through public dispatch. The shared registry enables the new selector, and legacy-only configurations keep their existing tool schemas. Discovery starts eligible probes concurrently, with one request per service and a shared five-second deadline. Completed results survive a stalled service. [Review findings](2026-11-06-1857-yandex-oauth-plan-review.md) · [Revised plan](../2026-11-06-yandex-oauth-implementation-plan.md)

The revised acceptance tests cover public dispatch, legacy schema compatibility, large retry delays, and partial results at the deadline. This iteration reviewed documentation. Runtime tests and live account checks are part of implementation.
