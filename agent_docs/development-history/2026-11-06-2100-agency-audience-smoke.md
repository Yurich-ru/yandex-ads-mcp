# 2026-11-06 — Agency and Audience read-only smoke tests

Tested the configured agency subclient through `yd_clients_get(client_login=...)` and the expected Audience segment through `yd_audience_segments_get(account_login=...)`. Requests were restricted to the Direct clients and Audience segments endpoints, with read-only mode enabled and body logging disabled.

- Direct used the primary OAuth token and the configured Client-Login header. Yandex returned HTTP 200 with error code 8800, so the agency target was not verified.
- Audience registry entry 1 returned HTTP 200 and contained the expected segment ID.
- Audience entry 2 returned HTTP 403. Entry 3 returned HTTP 200 without the expected segment.

No writes were attempted. The standalone Direct CLIENT case is still unverified.
