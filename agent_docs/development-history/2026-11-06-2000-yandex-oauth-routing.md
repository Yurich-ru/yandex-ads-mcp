# 2026-11-06 20:00 — Shared OAuth routing and safety gates

Completed Task 2. The MCP entry point validates selectors, resolves credentials and the Direct target, then applies the allowlist and confirmation gate. The shared registry enables the new schema field. An explicit Direct legacy selector keeps the original agency or standalone route.

Connected the resolved route to the existing request helpers in this task so a call accepted by the new selector uses the credential checked at the MCP boundary. Direct helpers use the resolved token and header in call context. Metrica and Audience receive the token through their existing third handler argument.

New checks failed before implementation. All 177 offline checks passed after implementation. An additional HTTPX mock confirmed the three services' authorization headers through public tool calls. The Windows sandbox required loopback access for Python's event loop. [Implementation plan](../2026-11-06-yandex-oauth-implementation-plan.md)

Task 3 still covers the remaining transport paths, concurrency, diagnostics, and per-candidate allowlist filtering in legacy inventory.
