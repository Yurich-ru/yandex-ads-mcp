# 2026-11-06 20:23 — Read-only OAuth smoke checks with local credentials

Loaded the user-designated local `.env` without printing secrets or real identities. Exercised normal registry startup via `server.main`, substituting only the stdio/server-run boundary to run the smoke calls in process. Calls used `server.call_tool` and real HTTPX transport. Enforced read-only mode, disabled logs, restricted outbound methods/hosts/paths to the inspected read endpoints, and bounded calls. No writes were attempted; `.env` and runtime code were not changed.

## Results

Labels below refer to registry entry order during this run, not account names.

| Case | Direct configured login match | Client ID matches separate token read | Direct account type | Metrica counters | Metrica permissions | Audience |
| --- | --- | --- | --- | --- | --- | --- |
| registry_1 | Pass | Pass | SUBCLIENT | 1 | edit | Read succeeded, 57 segments |
| registry_2 | Pass | Pass | SUBCLIENT | 5 | edit, own | HTTP 403 |
| registry_3 | Pass | Pass | SUBCLIENT | 3 | edit | Read succeeded, empty list |

For each Direct case, a minimal `Clients.get` read supplied Login, ClientId and Type, then the public `yd_clients_get(account_login=...)` result was checked against the configured login and baseline ClientId. Actual outgoing tool requests used the selected Bearer token and omitted Client-Login. Configured logins were independent expected values; client-ID baselines were obtained during this run, not supplied independently beforehand.

Metrica and Audience tool requests used the selected OAuth token. One counter in registry_2 matched its registry login through `owner_login`; no counter ownership match was established for the other two entries. No independently expected counter/segment IDs were supplied, so list success and resource counts do not establish the intended resource. Audience's empty list is a successful read, not identity evidence. The 403 establishes denial for that read, not a verified limited-resource account.

## Remaining release checks

- The configured accounts exercise Direct with their own tokens, but all are classified SUBCLIENT upstream. No CLIENT-type standalone account was verified.
- No legacy agency subclient was configured (`YD_LOGIN` and legacy map empty); the primary token equals one registry token. Do not label any of these calls as an agency-token-plus-Client-Login test. Need an appropriate agency credential and independently specified target subclient.
- Returned Metrica permissions were edit/own, not view. Need a controlled limited-permission account and independently expected resource, or an explicit clarification of the intended limited-permission criterion. Never test restrictions by attempting writes.
- The stale `.env.example` comment from the [final review](2026-11-06-2018-yandex-oauth-final-review.md) remains outstanding.

Live routing validation is partial; release acceptance is not complete. Offline verification remains the 261 passing checks recorded in the final review; no code changed during these live checks.
