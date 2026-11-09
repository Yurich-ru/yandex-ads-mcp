# 2026-11-06 20:18 — OAuth final diff review and live-test prerequisite check

Reviewed the current uncommitted OAuth implementation across `server.py`, the three service modules, offline tests, configuration template, README, and project contracts. The previous discovery-filter, legacy-schema, and discovery-deadline findings are addressed. No additional routing defect was identified in the paths examined; this does not replace live routing verification.

## Finding

- P2: `.env.example:28` still states that calls use legacy credentials and the registry is only validated. This is outdated: explicit `account_login` selects the registry, and `YANDEX_DEFAULT_LOGIN` changes unqualified Direct, Metrica, and Audience calls. Correct the comment before release so configuration guidance describes the active routing behavior. No implementation or template edits were made during this review.

## Verification

- `.venv/Scripts/python.exe -B test_safety.py`: exit 0, 261 PASS checks, ALL PASSED. Used the documented Windows sandbox exception for asyncio loopback; no live Yandex calls.
- `git diff --check`: exit 0.
- No YD/YC/YANDEX environment variable names were present in the current process. The workspace contained `.env.example` but no local `.env` credential file. Credential values were not printed or searched in unrelated locations.
- Live tests were not run. Requested the controlled credential/configuration location and independently expected identities/resources from the user. Release verification remains incomplete.

## Pending read-only smoke matrix

Run through the current MCP call boundary with `YD_READONLY=true`, body/file logging disabled, and the intended Direct allowlist. Load configuration before module initialization and initialize the registry via the normal startup path. Keep credentials and real identities out of repository artifacts; record only case labels and match outcomes.

| Case | Read call | Required evidence |
| --- | --- | --- |
| Standalone Direct | `yd_clients_get(account_login=...)` | Returned `Login` and `ClientId` match independently expected identity; selected registry route omits `Client-Login` |
| Legacy agency subclient | `yd_clients_get(client_login=...)` | Returned `Login` and `ClientId` match the expected subclient; login is absent from the legacy token map so this exercises the agency token plus header, including with a shared default configured |
| Limited-permission Metrica | `yd_metrika_counter_get(account_login=..., counter_id=...)` | Returned counter ID and expected ownership/site metadata match the preselected controlled resource; inspect permission metadata where available |
| Alternative limited-permission Audience | `yd_audience_segments_get(account_login=...)` | Expected segment ID and identifying metadata appear under the selected account; an empty list alone does not establish identity |

Use the Metrica or Audience case according to the controlled credentials supplied. Verify the limited-permission premise from trusted configuration or available read metadata, never by attempting a write. A successful status or discovery `read_verified` alone is insufficient. API failures or mismatches leave the corresponding case unverified.
