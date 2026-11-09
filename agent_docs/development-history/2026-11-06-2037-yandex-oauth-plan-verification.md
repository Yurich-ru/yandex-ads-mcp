# 2026-11-06 20:37 — Verify execution of the Yandex OAuth plan

Reviewed the [four implementation tasks](../2026-11-06-yandex-oauth-implementation-plan.md) against the current working tree.

## Findings

- A confirmed Direct update with `client_login=""` used the shared default credential and omitted `Client-Login`. The old route falls back to `YD_LOGIN`. The resolver treats the present but empty selector as omitted. Reproduced through `call_tool` and HTTPX MockTransport.
- Direct discovery classified HTTP 200 with `{"result":{"Clients":[{}]}}` as `read_verified` even though the probe requested `Login`. Reproduced through the public discovery tool. Empty lists are valid. A nonempty list with malformed entries should be `unavailable`. [Direct Clients.get reference](https://yandex.com/dev/direct/doc/en/clients/get)
- The `.env.example` registry comment still describes startup validation only. The plan footer predates the recorded live smoke checks.
- The schema regression test compares two current-code configurations. An independent comparison to the pre-change Git revision confirmed the legacy schema contract for this working tree.

## Verification

- `.venv/Scripts/python.exe -B test_safety.py` passed all 261 offline checks using the documented Windows loopback allowance. `git diff --check` passed.
- All 152 tool definitions matched revision `b3296cf` with a legacy Direct map and the shared registry disabled, with confirmation enabled and disabled.
- Existing smoke records cover three per-account Direct SUBCLIENT routes and a known Metrica counter with differing access. The standalone CLIENT and legacy agency-token-plus-header cases remain open. This review used mocked HTTP only. [Counter smoke results](2026-11-06-2024-yandex-oauth-counter-smoke.md)
