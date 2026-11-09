# 2026-11-06 20:14 — Add read-only service discovery

Task 4 added `yd_yandex_accounts_get` for configurations with a shared registry. Offline listings preserve the account filter. Live verification uses one selected account, respects the Direct allowlist, and makes up to three concurrent reads within a five-second probe deadline. Expired probes are cancelled and drained while completed results are retained.

Direct authorization classification follows the dedicated v5 error table, which assigns invalid tokens to code 53 and lists code 1002 as a general operation error. The general token guide assigns 1002 to invalid tokens. Discovery uses the more specific error table and treats generic failures as unavailable. [Direct error table](https://yandex.com/dev/direct/doc/en/concepts/errors-list) · [Direct token guide](https://yandex.com/dev/direct/doc/en/concepts/auth-token)

The full offline suite passed 261 checks, including malformed responses, denied access, empty success, rate limits, redirects and cancellation. A fresh review found a credential leak through JSON keys. A failing preview test reproduced it and passed after the fix. Live smoke tests need controlled credentials, which were unavailable in this session. [Implementation plan](../2026-11-06-yandex-oauth-implementation-plan.md)
