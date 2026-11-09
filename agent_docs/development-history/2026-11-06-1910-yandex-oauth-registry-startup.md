# 2026-11-06 19:10 — Shared OAuth registry startup

Completed Task 1 of the [implementation plan](../2026-11-06-yandex-oauth-implementation-plan.md).
Startup accepts the legacy token or a nonempty registry. It rejects malformed entries, duplicate
logins and an invalid explicit default without printing configuration values. No default is
inferred. Calls still use legacy credentials and stop locally when the selected service lacks one.
Legacy Direct inventory still requires the primary token until Task 3 updates its probes.

Installed the declared dependencies in the ignored Windows `.venv`. The offline baseline passed
111 checks. The new cases produced 26 expected failures before implementation, and the full suite
then passed all 140 checks. No provider calls were made.

Task 2 covers selector resolution and safety gates; Task 3 wires credentials into requests.
