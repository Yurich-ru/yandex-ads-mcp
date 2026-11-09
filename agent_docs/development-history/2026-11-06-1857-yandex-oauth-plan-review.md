# 2026-11-06 18:57 — Shared OAuth implementation plan review

Reviewed the [plan](../2026-11-06-yandex-oauth-implementation-plan.md) against the current request helpers, dispatch, tests, project invariants, and fresh first-party API documentation. The plan and runtime code were not changed.

## Findings

1. **P2 — Preserve the discovery filter across dispatch (plan lines 71, 89).** Task 2 strips MCP controls before dispatch and returns no resolved route for inventory. Task 4 needs `account_login` inside the discovery handler to filter offline results and authorize a single-account verification. Define an exception or pass a validated discovery selection separately; otherwise following the generic stripping rule loses the filter. Test through public `call_tool`, with two registry entries, for both filtered offline listing and `verify=true`.
2. **P2 — Reconcile legacy-only schema compatibility (lines 33, 44).** With only `YD_DIRECT_TOKENS` configured, line 33 adds `account_login`, while line 44 requires unchanged schemas without the new settings. Pick and document the intended exception or gate schema augmentation on the new registry. Test the exact legacy-only configuration, not just registry on/off with unspecified legacy settings. Also reconcile INV-003 with the new opt-in variables before implementation; its current condition mentions only legacy settings.
3. **P2 — Specify a total discovery deadline and retry budget (lines 89–90).** A finite HTTPX timeout does not bound a whole probe. The existing `request_with_retry` makes up to four attempts and honors an uncapped numeric `Retry-After`; adopting it can exceed both the stated three-probe request budget and a practical completion deadline. Specify a discovery-only attempt limit and a wall-clock deadline covering requests and backoff, while preserving normal Direct retry behavior. Test 429/503 with a large Retry-After and ensure other service results remain available.

## Verified foundations and limits

- The separation of token selection, Direct target, and Client-Login matches the [Direct agency documentation](https://yandex.com/dev/direct/doc/en/best-practice/agency). Bearer for Direct and OAuth for [Metrica](https://yandex.com/dev/metrika/en/intro/authorization) and [Audience](https://yandex.ru/dev/audience/en/intro/authorization) match current first-party documentation.
- [HTTPX documents](https://www.python-httpx.org/advanced/timeouts/) per-operation/inactivity timeouts, not a whole-call deadline. Retry behavior was inspected in `tools_direct_extra.py:20`.
- Registry labels deliberately remain operator assertions. This review does not claim that discovery proves account identity, resource access, or write permissions.
- Tried the existing offline suite using the available `python`, with YD/YC/YANDEX environment settings removed from the child process. It stopped at import with `ModuleNotFoundError: No module named 'httpx'`; no tests ran. No dependencies were installed and no live account was queried.

Resolve these plan contracts before implementation. The proposed routing architecture itself does not require replacement.
