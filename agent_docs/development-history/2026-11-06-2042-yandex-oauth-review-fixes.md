# 2026-11-06 20:42 — Fix OAuth acceptance-review defects

Addressed the [verification review](2026-11-06-2037-yandex-oauth-plan-verification.md) with targeted code, tests, and documentation changes.

- The resolver used truthiness to detect a legacy selector, conflating empty input with omission. It now tests presence via a non-None value; the public boundary normalizes explicit null to an empty legacy selector. Both preserve the original `YD_LOGIN` fallback and legacy map/agency behavior. Omission still permits the shared default. Added mocked confirmed-update, preview, and allowlist tests.
- Direct discovery previously accepted any dictionary as a list entry. Every nonempty entry must now include a nonblank string `Login`, the field requested by the probe. Missing, null, numeric, empty, whitespace-only, and mixed malformed entries return `unavailable`; an empty list remains `read_verified`.
- Replaced the stale `.env.example` comment and updated the plan footer with partial live evidence and outstanding cases.
- Generated frozen canonical schema fingerprints from Git revision `b3296cf` using synthetic configuration in a temporary directory. The offline suite now checks name, description, and inputSchema for all 152 legacy definitions with confirmation on/off, independently of current-code configuration comparisons. CI does not need Git history.

## Verification

The new discovery regressions failed before the response-validation fix. The first campaign regression fixture used the wrong argument shape and failed with KeyError; corrected it to `campaign_id`, temporarily removed the resolver fix, and verified that all four valid confirmed-update cases failed on wrong routing. Restoring the fix made them pass.

Final `.venv/Scripts/python.exe -B test_safety.py`: exit 0, 286 PASS checks, ALL PASSED, using the documented Windows asyncio loopback exception. `git diff --check` passed. No live API calls, credential changes, commit, or release occurred in this iteration.

Remaining release evidence: standalone Direct CLIENT and legacy agency-token-plus-Client-Login live checks. Existing SUBCLIENT and Metrica smoke records remain partial evidence, not full live acceptance.
