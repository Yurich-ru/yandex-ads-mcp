# 2026-11-06 20:08 — Verify Shared OAuth request routes

Task 3 exercised real HTTPX request construction through MCP tool calls. The cases cover Direct v5, v501, Reports, Metrica reports and writes, Audience reads and multipart uploads, and both Wordstat credential paths, including `userInfo`. Overlapping Direct calls and a failed call confirmed that each request keeps its own credential and that the next call resolves its default.

Legacy Direct inventory checks the actual target before every probe. A primary credential without an attributable login is skipped when the allowlist is set. Allowed mapped credentials can still be probed if the primary credential is absent or blocked.

The new offline checks initially reproduced 11 redaction and inventory failures. All 224 checks passed after the fixes. Task 4's review added a regression for credentials echoed in JSON keys and verified that normal exception logs exclude provider response details. [Implementation plan](../2026-11-06-yandex-oauth-implementation-plan.md)
