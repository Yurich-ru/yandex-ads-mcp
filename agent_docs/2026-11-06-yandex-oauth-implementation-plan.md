# Shared Yandex OAuth Registry Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task by task. Checkboxes track implementation.

**Goal:** Configure one OAuth token per Yandex account and select it consistently for Direct, Metrica, and Audience.

**Architecture:** Resolve credentials once per MCP call before dispatch. Keep the selected credential, the Direct target used for safety checks, and the optional Direct `Client-Login` header separate. Keep Wordstat on its current Cloud authorization path.

**Tech stack:** Python 3.10+, MCP 1.x, HTTPX, and the standard library.

**Spec:** The routing contract below records the proposed design. This plan does not change runtime behavior.

## Routing contract

Keep the shared registry and existing configuration. Extending only the Direct map would preserve the current split between services. Treating a registry as a replacement for all defaults would silently switch accounts and break agency routing and tokens issued with narrower permissions.

```dotenv
YANDEX_TOKENS=account1:token1,account2:token2
YANDEX_DEFAULT_LOGIN=account1
# Keep existing variables as needed, e.g. YD_OAUTH_TOKEN for agency calls.
```

| Call | Direct | Metrica | Audience |
| --- | --- | --- | --- |
| Explicit `account_login` with the shared registry enabled | Shared registry, then exact match in `YD_DIRECT_TOKENS`. Omit the `Client-Login` header. | Shared registry only | Shared registry only |
| Explicit `client_login` | Existing route: legacy map, otherwise `YD_OAUTH_TOKEN` plus `Client-Login`. An explicit legacy call bypasses the new default and registry. | Not advertised | Not advertised |
| No selector, `YANDEX_DEFAULT_LOGIN` set | Use that registry token, without `Client-Login` | Use that registry token | Use that registry token |
| No selector, no new default | Existing `YD_LOGIN` route: legacy map, otherwise `YD_OAUTH_TOKEN` and optional `Client-Login` | `YD_METRIKA_TOKEN`, then `YD_OAUTH_TOKEN` | `YD_AUDIENCE_TOKEN`, then `YD_OAUTH_TOKEN` |

- The new default must name a shared registry entry. Setting it intentionally changes unqualified calls to all three services, including Direct calls previously using `YD_LOGIN`. Leave it unset in a mixed setup until that switch is wanted. Never infer a default from registry order.
- A blank, malformed, or unknown explicit `account_login` fails before HTTP. Supplying both selectors on Direct is an error, even when they match. Selecting a registry credential for a different agency subclient is out of scope; the existing agency route stays available. A failure from Yandex never triggers a retry with a different credential.
- An explicitly supplied empty string for legacy `client_login` preserves the fallback to `YD_LOGIN`. Omit the selector to use the shared default. The MCP schema accepts strings and rejects JSON null.
- For Direct, the effective target used by `YD_ALLOWED_LOGINS` is the named cabinet or agency subclient even when there is no `Client-Login` header. Treat configured names as operator assertions, not proof of the upstream identity. Verify mappings with a read before allowing writes. Keep the existing no-login legacy behavior out of this migration; `YD_ALLOWED_LOGINS` is not a global account or resource boundary.
- A nonempty shared registry enables `account_login` on ordinary Direct, Metrica, and Audience tools. A legacy-only configuration continues to use `client_login` for Direct. Reject an explicit `account_login` when the shared registry is disabled or when the tool is Wordstat or legacy Direct inventory. Classify by the actual API. For example, `yd_audience_targets_add` is Direct. The new discovery tool defines `account_login` as its own registry filter.
- Direct retains `Authorization: Bearer`; Metrica and Audience retain `Authorization: OAuth`. Counter IDs and Audience resource IDs remain separate arguments. Reuse a token across services only if its grants and the account's resource permissions permit it. Keep separate defaults for services that need different credentials.

Verified against Yandex's [Direct request format](https://yandex.com/dev/direct/doc/en/format),
[agency routing](https://yandex.com/dev/direct/doc/en/best-practice/agency),
[Metrica authorization](https://yandex.com/dev/metrika/en/intro/authorization), and
[Audience authorization](https://yandex.ru/dev/audience/en/intro/authorization).
These sources describe API authorization; this review did not verify any live account.

## Global constraints

- Preserve the full legacy tool list and schemas whenever the shared registry is disabled, including when only `YD_DIRECT_TOKENS` is set. Legacy Direct inventory must enforce a configured Direct allowlist on each probe. Keep Python 3.10+ and the existing dependencies.
- Do not put tokens or raw configuration in logs, errors, previews, inventory, or test output. Report invalid registry entries by position. Use placeholders in the repository.
- Keep `YD_READONLY`, `YD_CONFIRM`, Direct retry behavior, and partial-success reporting. Resolve and gate before HTTP; preview the effective route without its token.
- Do not change Wordstat API-key precedence, OAuth-to-IAM exchange, or caching. Never send a registry token to Cloud.

## Review focus

- Duplicate registry names or an invalid default must fail startup instead of changing identity (task 1).
- A new default must not replace an explicit legacy agency selector (task 2).
- Both a registry token without a header and legacy discovery fan-out must honor Direct's configured allowlist (tasks 2–3).
- Overlapping calls and exceptions must never carry credentials from one account into another (task 3).
- A legacy-only Direct map must preserve the current schemas, including `client_login` and the existing conditions for `confirm` (task 2).
- The public discovery entry point must retain the selected account for filtering and verification (task 4).
- A successful empty read, a provider denial, and a temporary failure must produce distinct discovery results (task 4).
- A rate limit or a stalled service must leave other discovery results available within the shared probe deadline (task 4).

## Task 1: Parse registry configuration and update startup

**Files:** `server.py` (environment config, `main`), `test_safety.py`, `.env.example`, `README.md`.

- [x] Add offline cases for an empty registry, two entries, whitespace around fields, a missing value, duplicate login, header control characters, and a default missing from the shared map. Assert failure output excludes the raw value and token. Test startup with the original token, only a valid registry, and neither.
- [x] Run `python3 test_safety.py`; confirm the new registry cases fail before implementation.
- [x] Add `_parse_yandex_tokens(raw: str) -> dict[str, str]` and `_validate_yandex_default(login: str, tokens: dict[str, str]) -> str`. Split each comma-delimited entry on its first colon; trim fields, use exact case-sensitive login matching, and reject empty entries or fields, duplicates, and control characters. Leave the permissive legacy parser unchanged. At startup accept the legacy token or a nonempty shared registry. A registry with no default starts, but unqualified calls with no applicable legacy credential return a configuration error locally. Preserve the old no-credential startup failure when the registry is absent.
- [x] Run the offline suite; require exit 0. Document the new settings and that the default is explicit.

## Task 2: Resolve account selection and apply the safety gates

**Files:** `server.py` (`_is_direct`, `_augment_schema`, `call_tool`, `_confirm_preview`), `test_safety.py`, `agent_docs/invariants.md`.

- [x] Add offline dispatch cases for each routing row, same login in both maps with different tokens, both selectors, unknown login, a legacy-only login on Metrica, a Direct registry account blocked by the allowlist, and a missing credential. Assert no dispatch on denial. With `YD_DIRECT_TOKENS=legacy:legacy-token` and both new settings unset, assert that the public tool list and schemas match the pre-change baseline with the same safety flags. Confirm `client_login=legacy` still selects its token and `account_login=legacy` is rejected before HTTP. With the shared registry configured, assert the schema gate for Direct, Direct AudienceTargets, Audience API, Metrica, Wordstat, and legacy inventory.
- [x] Add a small private resolved route in `server.py` carrying `token` (excluded from repr), `account_login`, `direct_target_login`, and `client_login_header`. Define `_resolve_auth(name: str, account_login: str | None, client_login: str | None) -> _ResolvedAuth | None`. `None` means the tool owns its credential handling (Wordstat or inventory). Validate supplied selectors at the tool boundary before using this interface. Distinguish omission from a supplied blank or null. Strip routing controls from ordinary service arguments before dispatch. Task 4 defines the dedicated discovery branch that preserves the tool's filter.
- [x] Enforce read-only first, then validate and resolve, then check the resolved Direct target, then generate any confirmation preview. Preserve existing preview fields for legacy callers. For a named account route, show the account in `account_login` and use a separate display-only `direct_target_login` for Direct; leave preview `client_login` null so it does not suggest passing both selectors. Assert previews and denied calls never reach the network or contain tokens.
- [x] Extend INV-003 in `agent_docs/invariants.md` to cover both new variables and explicitly preserve schemas for legacy-only configurations. Run the offline suite before and after the resolver change. Require the new cases to fail first and pass after wiring.

## Task 3: Use the resolved credential in every request path

**Files:** `server.py`, `tools_direct_extra.py`, `tools_metrika.py`, `tools_audience.py`, `test_safety.py`, `README.md`, `agent_docs/architecture.md`.

- [x] Capture real request construction with synthetic tokens and an HTTPX mock transport or async client stub. Cover Direct v5, v501 (including the extra module), Reports, Metrica report and mutation, Audience read and multipart upload, both Wordstat paths (including extra user info), and legacy Direct discovery. Assert exact headers, unchanged resource parameters, no forwarded MCP controls, and no credential change on a 401/403. Run two overlapping calls with distinct accounts and verify the next call after an exception uses its own default.
- [x] For Direct, replace login-only per-call state with a resolved `(token, client_login_header)` in the existing request context pattern. `call_tool` sets it once and resets it in `finally`; both core and extra Direct v5/v501 and Reports use `direct_headers()`. Keep shared config immutable across calls and preserve Sandbox/custom endpoint routing. A configured custom Direct endpoint must be trusted with the credentials sent to it.
- [x] Pass `_ResolvedAuth` explicitly into `_dispatch`. Pass its token as the third positional argument to the existing Metrica/Audience handlers; remove their captured defaults so every registered handler requires the per-call token. Keep resource and upload handling intact. Retain service-specific auth formats. Check exception logging and optional body logging across all selected-account paths: mask credentials if upstream errors echo them. Update the Audience 403 hint for both config styles.
- [x] Keep the legacy `yd_direct_accounts_get` response shape and legacy candidate sources. Skip absent credentials and, with an allowlist enabled, skip candidates whose Direct target is disallowed or cannot be attributed. Filter before building each request; checking the inbound tool arguments cannot protect fan-out. Preserve Wordstat's API-key and legacy IAM routes; if both credentials are absent in a registry-only process, return a local configuration error before Cloud I/O.
- [x] Run the new network-boundary cases red then green and run the full offline suite. Document mixed migration: add the registry with no new default; verify explicit reads using `account_login`; migrate callers; set the shared default only if all three services should switch. Keep existing vars needed by agency/Wordstat. To roll back, remove new settings and revert migrated selectors.

## Task 4: Add optional live service discovery after routing is verified

**Files:** `server.py`, `test_safety.py`, `README.md`, `agent_docs/architecture.md`.

- [x] Add `yd_yandex_accounts_get` as read-only and non-Direct. Expose it only with the shared registry configured. In `call_tool`, handle this tool before generic account selector extraction. Validate its optional `account_login` against the shared registry and require a known login for `verify=true`. Dispatch with the validated filter and `verify` flag in the tool arguments and no resolved service route. With verification disabled, return matching registry names, the configured default, and service states of `not_checked` without network I/O. A call with no filter lists the full registry.
- [x] Probe Direct `Clients.get` without `Client-Login` (subject to the Direct allowlist and configured endpoint), Metrica `GET /management/v1/counters`, and Audience `GET /v1/management/segments`. Start eligible probes concurrently under one five-second deadline measured on the event loop's monotonic clock. Each service gets one HTTP attempt with a finite HTTPX timeout and redirects disabled. Use direct HTTP calls for discovery so retry helpers and `Retry-After` cannot extend the budget. At the deadline, cancel and drain pending probe tasks, mark those services `unavailable`, and retain the results already received. The deadline bounds the probe work, with cooperative cancellation and cleanup at expiry. [HTTPX timeout behavior](https://www.python-httpx.org/advanced/timeouts/) · [Python 3.10 asyncio task waiting](https://docs.python.org/3.10/library/asyncio-task.html#asyncio.wait)
- [x] Return `read_verified`, `access_denied`, `unavailable`, or `not_checked` per service. A valid empty list counts as a successful read. Map provider authorization failures, including Direct JSON errors over HTTP 200, to `access_denied`. Map transient failures and malformed responses to `unavailable`. Return sanitized status only. A successful probe verifies read access to its endpoint.
- [x] Test through public `call_tool` with two registry entries. Assert that a filtered offline listing returns only the selected login with zero HTTP calls. Verify the same login and assert that all outgoing requests use its credential. Check that missing or unknown verification filters fail before HTTP. Stub positive, empty, denied, non-JSON, and mixed results. For both HTTP 429 and 503 with a large `Retry-After`, assert one request to the failing service, no retry wait, and results from the other services. Use a shortened test deadline with a cancellable stalled probe to verify that completed results survive and the stalled service becomes `unavailable`. Assert at most three requests, no probe for a blocked Direct candidate, and no payload or token in output.
- [x] Verify schema and read-only classification. Document discovery in both README tool lists, update the tool count, and point users to `yd_metrika_counters_get(account_login=...)` for actual counters.
- [x] Run `python3 test_safety.py` and `git diff --check`. Use controlled, read-only accounts for live smoke tests if available: a standalone Direct account, an agency subclient on the legacy route, and a Metrica or Audience account with limited grants. Do not mutate production. At release, update `pyproject.toml`, `CHANGELOG.md`, and the versioned release commit per project rules.

Offline verification passed 286 checks, including regressions for empty legacy selectors, internal handler calls with `None`, and malformed Direct discovery entries. Nonempty Direct discovery results must contain a nonblank string `Login` in every entry; a valid empty list remains successful. Frozen fingerprints independently guard all 152 legacy tool definitions against revision `b3296cf` with confirmation enabled and disabled. `git diff --check` passed.

Live checks verified three per-account Direct `SUBCLIENT` routes and an independently specified Metrica counter with differing account access. The read-only legacy agency check also passed. A standalone Direct `CLIENT` remains open. See the [counter smoke record](development-history/2026-11-06-2024-yandex-oauth-counter-smoke.md) and the [agency smoke result](development-history/2026-11-06-2052-legacy-agency-smoke-pass.md). Release metadata will be updated when cutting the release.
