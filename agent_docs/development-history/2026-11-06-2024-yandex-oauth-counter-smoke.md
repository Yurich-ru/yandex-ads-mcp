# 2026-11-06 20:24 — Verify the independently specified Metrica counter

The user supplied an expected counter ID privately. It is intentionally omitted from repository artifacts. Loaded credentials from the designated local `.env` and called `yd_metrika_counter_get(account_login=..., counter_id=...)` through normal registry startup and the public call boundary for each registry entry. Enforced read-only mode, disabled logs, restricted transport to GET on that exact official Metrica counter endpoint, and bounded execution. No writes were attempted.

| Registry entry | HTTP status | Returned ID matches expected counter | Reported permission | Selected token matches outgoing header |
| --- | --- | --- | --- | --- |
| registry_1 | 403 | Not returned | Not available | Yes |
| registry_2 | 200 | Yes | edit | Yes |
| registry_3 | 200 | Yes | edit | Yes |

The successful responses verify the independently expected resource. The denied response demonstrates a resource-access difference between configured accounts. Neither successful response identified the registry login as the counter owner. These are resource-routing checks, not proof of OAuth subject identity or read-only token grants. The previous assumption that a limited-permission smoke case necessarily required `view` permission was too restrictive: the requested reads can also establish resource-level access restrictions. No write test is needed or authorized.

Metrica resource selection now has live evidence. A CLIENT-type standalone Direct account and the legacy agency-token-plus-Client-Login route remain unverified. The earlier configuration-template finding remains open. See [initial live results](2026-11-06-2023-yandex-oauth-live-smoke.md).
