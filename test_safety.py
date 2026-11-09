#!/usr/bin/env python3
"""Offline smoke tests for the fork's safety/access additions.

No network and no credentials required. Verifies:
  - mutating vs read-only tool classification
  - Direct vs Metrika/Wordstat classification
  - YD_LOG_FILE no longer writes a log file by default
  - partial-success annotation of Direct responses
  - tool schemas expose client_login (and confirm when YD_CONFIRM=on)

Run: python3 test_safety.py
"""
import os
import sys
import asyncio
import io
import json
import logging
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx

# Token must be present for the module to import cleanly under some setups.
os.environ.setdefault("YD_OAUTH_TOKEN", "test-token")
os.environ["YD_CONFIRM"] = "true"  # so schemas advertise the confirm flag
# The fallback checks below assume per-service tokens are NOT set.
os.environ.pop("YD_METRIKA_TOKEN", None)
os.environ.pop("YD_AUDIENCE_TOKEN", None)
# Extra Direct cabinets: one valid entry, one malformed (must be skipped).
os.environ["YD_DIRECT_TOKENS"] = "e-2:tok-two, broken-entry ,:no-login"

import server  # noqa: E402
from tools_direct_extra import annotate_partial  # noqa: E402

failures = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        failures.append(name)


print("== mutating classification ==")
MUTATING = [
    "yd_campaigns_add", "yd_campaigns_update", "yd_campaigns_action",
    "yd_ads_update", "yd_keyword_bids_set_auto", "yd_bid_modifiers_toggle",
    "yd_callouts_link", "yd_metrika_label_link", "yd_metrika_goal_delete",
    "yd_metrika_grant_add", "yd_metrika_upload_conversions", "yd_videos_upload",
    "yd_excluded_sites_update", "yd_blocked_ips_update", "yd_campaign_strategy_update",
    "yd_keywords_update",
    "yd_keywords_suspend", "yd_keywords_resume", "yd_keywords_delete",
    "yd_audience_targets_suspend", "yd_audience_targets_resume",
    "yd_retargeting_lists_update",
    "yd_audience_segment_upload", "yd_audience_segment_confirm",
    "yd_audience_segment_reprocess", "yd_audience_segment_create_lookalike",
    "yd_audience_grant_add", "yd_audience_grant_delete",
    "yd_audience_pixel_undelete", "yd_audience_delegate_add",
]
READONLY = [
    "yd_campaigns_get", "yd_keywords_research", "yd_keywords_has_volume",
    "yd_report", "yd_changes_check", "yd_wordstat_top_requests",
    "yd_wordstat_regions_tree", "yd_metrika_report", "yd_metrika_report_comparison",
    "yd_metrika_counters_get", "yd_metrika_conversions_status",
    "yd_excluded_sites_get", "yd_regions_get", "yd_interests_get",
    "yd_changes_timestamp_get",
    "yd_audience_segments_get", "yd_audience_pixels_get",
    "yd_audience_grants_get", "yd_audience_accounts_get", "yd_audience_delegates_get",
]
for n in MUTATING:
    check(f"{n} is mutating", server._is_mutating(n) is True)
for n in READONLY:
    check(f"{n} is read-only", server._is_mutating(n) is False)

print("== direct vs metrika/wordstat/audience ==")
check("yd_campaigns_add is direct", server._is_direct("yd_campaigns_add") is True)
check("yd_vcards_add is direct", server._is_direct("yd_vcards_add") is True)
check("yd_metrika_report not direct", server._is_direct("yd_metrika_report") is False)
check("yd_wordstat_top_requests not direct", server._is_direct("yd_wordstat_top_requests") is False)
check("yd_audience_targets_add is direct (Direct API AudienceTargets)",
      server._is_direct("yd_audience_targets_add") is True)
check("yd_audience_segments_get not direct (Audience API)",
      server._is_direct("yd_audience_segments_get") is False)

print("== per-service token fallback ==")
check("METRIKA_TOKEN falls back to TOKEN", server.METRIKA_TOKEN == server.TOKEN)
check("AUDIENCE_TOKEN falls back to TOKEN", server.AUDIENCE_TOKEN == server.TOKEN)

print("== per-login Direct tokens (YD_DIRECT_TOKENS) ==")
from tools_direct_extra import client_login_var, resolve_direct_auth  # noqa: E402
check("token map parsed, malformed entries skipped", server.DIRECT_TOKENS == {"e-2": "tok-two"})
check("no login -> default token, no Client-Login",
      resolve_direct_auth(server.TOKEN, "") == (server.TOKEN, ""))
_ctx = client_login_var.set("e-2")
try:
    check("mapped login -> its own token, no Client-Login",
          resolve_direct_auth(server.TOKEN, "") == ("tok-two", ""))
    check("mapped login: server._headers() has no Client-Login",
          "Client-Login" not in server._headers() and server._headers()["Authorization"] == "Bearer tok-two")
finally:
    client_login_var.reset(_ctx)
_ctx = client_login_var.set("agency-sub")
try:
    check("unmapped login -> default token + Client-Login (agency path)",
          resolve_direct_auth(server.TOKEN, "") == (server.TOKEN, "agency-sub"))
finally:
    client_login_var.reset(_ctx)
check("yd_direct_accounts_get is read-only", server._is_mutating("yd_direct_accounts_get") is False)

print("== logging default ==")
check("no log file written by default", server.LOG_FILE == "" and
      not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(server.__file__)), "yandex-ads.log")))

print("== partial-success annotation ==")
ok = annotate_partial({"result": {"AddResults": [{"Id": 1}]}})
check("clean result has no _partial_success", "_partial_success" not in ok)
bad = annotate_partial({"result": {"AddResults": [
    {"Id": 1},
    {"Errors": [{"Code": 5, "Message": "Bad text"}]},
    {"Warnings": [{"Code": 9, "Message": "Truncated"}]},
]}})
ps = bad.get("_partial_success", {})
check("errors detected", ps.get("error_count") == 1)
check("warnings detected", ps.get("warning_count") == 1)
check("ok flag false on error", ps.get("ok") is False)

print("== schema augmentation ==")
tools = {t.name: t for t in asyncio.run(server.list_tools())}
add_props = tools["yd_campaigns_add"].inputSchema["properties"]
check("direct tool exposes client_login", "client_login" in add_props)
check("mutating tool exposes confirm (YD_CONFIRM on)", "confirm" in add_props)
metrika_props = tools["yd_metrika_report"].inputSchema["properties"]
check("metrika report has no client_login", "client_login" not in metrika_props)
aud_props = tools["yd_audience_segment_delete"].inputSchema["properties"]
check("audience tool has no client_login", "client_login" not in aud_props)
check("mutating audience tool exposes confirm (YD_CONFIRM on)", "confirm" in aud_props)
check("all audience tools dispatched", len(server._audience_dispatch) == len(server.AUDIENCE_TOOLS))
bid_modifier_get_schema = tools["yd_bid_modifiers_get"].inputSchema
check("bid modifiers get exposes adjustment levels",
      bid_modifier_get_schema["properties"]["levels"]["items"].get("enum") ==
      ["CAMPAIGN", "AD_GROUP"])
autotargeting_schema = tools["yd_keywords_update"].inputSchema["properties"]["keywords"]["items"]
check("keywords update requires autotargeting settings",
      autotargeting_schema.get("required") == ["id", "autotargeting_settings"])
check("autotargeting schema exposes all search categories",
      set(autotargeting_schema["properties"]["autotargeting_settings"]
          ["properties"]["categories"]["properties"]) ==
      {"exact", "narrow", "alternative", "accessory", "broader"})
check("autotargeting schema exposes all brand options",
      set(autotargeting_schema["properties"]["autotargeting_settings"]
          ["properties"]["brand_options"]["properties"]) ==
      {"without_brands", "with_advertiser_brand", "with_competitors_brand"})

print("== responsive ads ==")
responsive_schema = tools["yd_ads_update"].inputSchema["properties"]["ads"]["items"]["properties"]["responsive_ad"]
check("responsive update requires titles and texts", responsive_schema.get("required") == ["titles", "texts"])
check("responsive update allows up to 7 titles", responsive_schema["properties"]["titles"].get("maxItems") == 7)
check("responsive update requires href or business_id",
      responsive_schema.get("anyOf") == [{"required": ["href"]}, {"required": ["business_id"]}])
ad_id_variants = [{"type": "string", "pattern": "^[0-9]+$"}, {"type": "integer"}]
check("responsive update accepts decimal string ad IDs",
      tools["yd_ads_update"].inputSchema["properties"]["ads"]["items"]["properties"]["id"].get("anyOf") == ad_id_variants)
check("ads get accepts decimal string ad IDs",
      tools["yd_ads_get"].inputSchema["properties"]["ad_ids"]["items"].get("anyOf") == ad_id_variants)
check("ads action accepts decimal string ad IDs",
      tools["yd_ads_action"].inputSchema["properties"]["ad_ids"]["items"].get("anyOf") == ad_id_variants)

captured_api_calls = []
captured_api_versions = []


async def capture_api(client, service, method, params):
    captured_api_calls.append((service, method, params))
    return {"result": {}}


async def capture_api501(client, service, method, params):
    captured_api_versions.append("v501")
    return await capture_api(client, service, method, params)


original_api = server._api
original_api501 = server._api501
server._api = capture_api
server._api501 = capture_api501
try:
    asyncio.run(server._handle_ads_update(None, {"ads": [{
        "id": 123,
        "responsive_ad": {
            "titles": ["First title", "Second title"],
            "texts": ["Ad text"],
            "href": "https://example.com",
            "sitelink_set_id": 789,
        },
    }]}))
    responsive_payload = captured_api_calls[-1][2]["Ads"][0]
    check("responsive update uses ResponsiveAd", "ResponsiveAd" in responsive_payload and "TextAd" not in responsive_payload)
    check("responsive update uses v501 API", captured_api_versions[-1] == "v501")
    check("responsive update sends all title assets",
          responsive_payload["ResponsiveAd"]["Titles"] == ["First title", "Second title"])
    check("responsive update sends all text assets",
          responsive_payload["ResponsiveAd"]["Texts"] == ["Ad text"])
    check("responsive update sends sitelink set",
          responsive_payload["ResponsiveAd"]["SitelinkSetId"] == 789)

    asyncio.run(server._handle_ads_update(None, {"ads": [{
        "id": 124,
        "responsive_ad": {
            "titles": ["Business title"],
            "texts": ["Business text"],
            "business_id": 456,
        },
    }]}))
    business_payload = captured_api_calls[-1][2]["Ads"][0]["ResponsiveAd"]
    check("responsive update supports business profile without href",
          business_payload.get("BusinessId") == 456 and "Href" not in business_payload)

    calls_before_conflict = len(captured_api_calls)
    conflict_rejected = False
    try:
        asyncio.run(server._handle_ads_update(None, {"ads": [{
            "id": 123,
            "title": "Legacy title",
            "responsive_ad": {
                "titles": ["Responsive title"],
                "texts": ["Ad text"],
                "href": "https://example.com",
            },
        }]}))
    except ValueError:
        conflict_rejected = True
    check("mixed text and responsive fields are rejected before API call",
          conflict_rejected and len(captured_api_calls) == calls_before_conflict)

    calls_before_missing_destination = len(captured_api_calls)
    destination_rejected = False
    try:
        asyncio.run(server._handle_ads_update(None, {"ads": [{
            "id": 123,
            "responsive_ad": {
                "titles": ["Responsive title"],
                "texts": ["Ad text"],
            },
        }]}))
    except ValueError:
        destination_rejected = True
    check("responsive update requires destination before API call",
          destination_rejected and len(captured_api_calls) == calls_before_missing_destination)

    large_ad_id = "1921132329195330424"
    asyncio.run(server._handle_ads_get(None, {"ad_ids": [large_ad_id]}))
    get_params = captured_api_calls[-1][2]
    check("ads get requests responsive titles and texts",
          {"Titles", "Texts"}.issubset(get_params["ResponsiveAdFieldNames"]))
    check("ads get requests responsive sitelink set",
          "SitelinkSetId" in get_params["ResponsiveAdFieldNames"])
    check("ads get uses v501 API", captured_api_versions[-1] == "v501")
    check("ads get keeps legacy text ad fields", "TextAdFieldNames" in get_params)
    check("ads get preserves unsafe integer IDs",
          get_params["SelectionCriteria"]["Ids"] == [1921132329195330424])

    asyncio.run(server._handle_ads_action(None, {"ad_ids": [large_ad_id], "action": "moderate"}))
    check("ads action preserves unsafe integer IDs",
          captured_api_calls[-1][2]["SelectionCriteria"]["Ids"] == [1921132329195330424])

    asyncio.run(server._handle_bid_modifiers_get(None, {
        "campaign_ids": [101],
        "ad_group_ids": [202],
    }))
    bid_modifier_params = captured_api_calls[-1][2]
    check("bid modifiers get supplies required levels",
          bid_modifier_params["SelectionCriteria"].get("Levels") == ["CAMPAIGN", "AD_GROUP"])
    check("bid modifiers get requests valid top-level fields",
          bid_modifier_params["FieldNames"] == ["Id", "CampaignId", "AdGroupId", "Level", "Type"])
    check("bid modifiers get requests type-specific fields separately",
          bid_modifier_params["DemographicsAdjustmentFieldNames"] ==
          ["Gender", "Age", "BidModifier", "Enabled"] and
          bid_modifier_params["RegionalAdjustmentFieldNames"] ==
          ["RegionId", "BidModifier", "Enabled"])

    asyncio.run(server._handle_bid_modifiers_get(None, {
        "campaign_ids": [101],
        "levels": ["AD_GROUP"],
    }))
    check("bid modifiers get respects explicit levels",
          captured_api_calls[-1][2]["SelectionCriteria"]["Levels"] == ["AD_GROUP"])

    settings = {
        "categories": {
            "exact": "YES",
            "narrow": "YES",
            "alternative": "NO",
            "accessory": "NO",
            "broader": "NO",
        },
        "brand_options": {
            "without_brands": "YES",
            "with_advertiser_brand": "YES",
            "with_competitors_brand": "NO",
        },
    }
    asyncio.run(server._handle_keywords_add(None, {"keywords": [{
        "ad_group_id": 202,
        "keyword": "---autotargeting",
        "autotargeting_settings": settings,
    }]}))
    added_autotargeting = captured_api_calls[-1][2]["Keywords"][0]
    check("keywords add sends autotargeting settings",
          added_autotargeting["AutotargetingSettings"]["Categories"]["Exact"] == "YES" and
          added_autotargeting["AutotargetingSettings"]["BrandOptions"]["WithoutBrands"] == "YES")

    asyncio.run(server._handle_keywords_update(None, {"keywords": [{
        "id": 303,
        "autotargeting_settings": settings,
    }]}))
    updated_autotargeting = captured_api_calls[-1][2]["Keywords"][0]
    check("keywords update sends autotargeting ID and settings",
          updated_autotargeting["Id"] == 303 and
          updated_autotargeting["AutotargetingSettings"]["Categories"]["Narrow"] == "YES" and
          updated_autotargeting["AutotargetingSettings"]["BrandOptions"]["WithCompetitorsBrand"] == "NO")

    asyncio.run(server._handle_keywords_get(None, {"ad_group_ids": [202]}))
    autotargeting_get_params = captured_api_calls[-1][2]
    check("keywords get requests all autotargeting categories",
          autotargeting_get_params["AutotargetingSettingsCategoriesFieldNames"] ==
          ["Exact", "Narrow", "Alternative", "Accessory", "Broader"])
    check("keywords get requests all autotargeting brand options",
          autotargeting_get_params["AutotargetingSettingsBrandOptionsFieldNames"] ==
          ["WithoutBrands", "WithAdvertiserBrand", "WithCompetitorsBrand"])
finally:
    server._api = original_api
    server._api501 = original_api501

print("== IAM expiresAt parsing ==")
from tools_direct_extra import iam_expiry  # noqa: E402
# 2026-05-30T13:00:00Z == epoch 1780146000
exp = iam_expiry({"expiresAt": "2026-05-30T13:00:00Z"}, now=0)
check("parses ISO Z to epoch (minus 60s safety)", abs(exp - (1780146000 - 60)) < 2)
exp_ns = iam_expiry({"expiresAt": "2026-05-30T13:00:00.123456789Z"}, now=0)
check("tolerates nanosecond precision", abs(exp_ns - (1780146000 - 60)) < 2)
fb = iam_expiry({}, now=1000)
check("falls back to now+11h when no expiresAt", fb == 1000 + 11 * 3600)
bad = iam_expiry({"expiresAt": "not-a-date"}, now=2000)
check("falls back on unparseable value", bad == 2000 + 11 * 3600)

print("== shared Yandex OAuth registry ==")
parse_registry = getattr(server, "_parse_yandex_tokens", lambda raw: None)
check("empty registry is disabled", parse_registry("") == {} and parse_registry("   ") == {})
check("two entries use case-sensitive logins",
      parse_registry("demo:sample-one,Demo:sample-two") ==
      {"demo": "sample-one", "Demo": "sample-two"})
check("registry trims fields and splits on the first colon",
      parse_registry(" demo : sample:one , other : sample-two ") ==
      {"demo": "sample:one", "other": "sample-two"})

for label, raw, position in [
    ("missing colon", "demo:sample-one,invalid-entry", 2),
    ("missing token", "demo:sample-one,other: ", 2),
    ("missing login", "demo:sample-one,:sample-two", 2),
    ("duplicate login", "demo:sample-one, demo:sample-two", 2),
    ("empty entry", "demo:sample-one,", 2),
    ("control character in login", "dem\ro:sample-one", 1),
    ("control character at token boundary", "demo:sample-one\n", 1),
    ("tab in registry", "\t", 1),
    ("DEL in token", "demo:sample-one\x7f", 1),
]:
    message = ""
    try:
        parse_registry(raw)
    except ValueError as exc:
        message = str(exc)
    check(f"{label} is rejected without exposing config",
          f"YANDEX_TOKENS entry {position}" in message
          and all(value not in message for value in (raw, "sample-one", "sample-two", "invalid-entry")))


@asynccontextmanager
async def offline_stdio():
    yield None, None


async def check_startup(label, token, registry, default, expected_error=""):
    with (patch.dict(os.environ, YANDEX_TOKENS=registry, YANDEX_DEFAULT_LOGIN=default),
          patch.multiple(server, create=True, TOKEN=token, YANDEX_TOKENS={}, YANDEX_DEFAULT_LOGIN=""),
          patch.object(server, "stdio_server", offline_stdio),
          patch.object(server.server, "run", new_callable=AsyncMock) as run,
          patch.object(server, "logger") as log):
        exit_code = 0
        try:
            await server.main()
        except SystemExit as exc:
            exit_code = exc.code
        errors = " ".join(str(arg) for call in log.error.call_args_list for arg in call.args)
        if expected_error:
            check(label, exit_code == 1 and not run.called and expected_error in errors
                  and all(value not in errors for value in (registry, default, "sample-one") if value))
        else:
            check(label, exit_code == 0 and run.await_count == 1 and not errors)
            if registry:
                check("startup loads the registry and only the configured default",
                      server.YANDEX_TOKENS == {"demo": "sample-one"}
                      and server.YANDEX_DEFAULT_LOGIN == default.strip())


async def check_registry_startup():
    await check_startup("legacy token still starts the server", "legacy-token", "", "")
    await check_startup("registry alone starts without a default", "", "demo:sample-one", "")
    await check_startup("registry starts with an explicit default", "", "demo:sample-one", " demo ")
    await check_startup("startup without either credential source fails", "", "", "",
                        "YD_OAUTH_TOKEN environment variable is required")
    await check_startup("default absent from registry fails even with a legacy token",
                        "legacy-token", "demo:sample-one", "Demo", "YANDEX_DEFAULT_LOGIN")
    await check_startup("a default requires a registry", "legacy-token", "", "absent-account",
                        "YANDEX_DEFAULT_LOGIN")
    await check_startup("control characters in the default fail startup",
                        "legacy-token", "demo:sample-one", "demo\t", "YANDEX_DEFAULT_LOGIN")
    await check_startup("malformed registry fails startup even with a legacy token",
                        "legacy-token", "demo:sample-one,invalid-entry", "", "YANDEX_TOKENS entry 2")


asyncio.run(check_registry_startup())


async def check_missing_legacy_credentials():
    with (patch.multiple(server, create=True, YANDEX_TOKENS={"demo": "sample-one"},
                         YANDEX_DEFAULT_LOGIN="", TOKEN="", METRIKA_TOKEN="", AUDIENCE_TOKEN="",
                         YC_API_KEY="", LOGIN="", DIRECT_TOKENS={}),
          patch.object(server, "_dispatch", new_callable=AsyncMock, return_value=[]) as dispatch):
        for name in ("yd_campaigns_get", "yd_metrika_counters_get", "yd_audience_segments_get",
                     "yd_wordstat_regions_tree", "yd_wordstat_user_info", "yd_direct_accounts_get"):
            response = await server.call_tool(name, {})
            data = json.loads(response[0].text) if response else {}
            check(f"{name} without an applicable credential fails before dispatch",
                  not dispatch.called and "credential" in data.get("error", "").lower()
                  and "sample-one" not in response[0].text)
        dispatch.reset_mock()
        with patch.multiple(server, LOGIN="legacy", DIRECT_TOKENS={"legacy": "legacy-token"}):
            await server.call_tool("yd_campaigns_get", {})
            check("an applicable legacy Direct credential still dispatches", dispatch.await_count == 1)


asyncio.run(check_missing_legacy_credentials())


async def check_account_routing():
    # Frozen from b3296cf: name, description and inputSchema, sorted by tool name.
    # Canonical JSON ignores formatting/key order; CI does not need Git history.
    legacy_fingerprints = {
        False: "70ddc5ec5b35f5409c561cf614c35802c01a501bb43154c064d04b8ca5208a06",
        True: "c9fa5b33ab3ef63328dac40f3aa9e3b98c689659b983e0a282718703c83e63de",
    }
    for confirm, expected in legacy_fingerprints.items():
        with patch.multiple(server, YANDEX_TOKENS={}, YANDEX_DEFAULT_LOGIN="", CONFIRM=confirm,
                            DIRECT_TOKENS={"legacy": "placeholder"}):
            tools = await server.list_tools()
        payload = [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                   for t in sorted(tools, key=lambda t: t.name)]
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                          separators=(",", ":")).encode()).hexdigest()
        check(f"152 legacy definitions match b3296cf with confirm={confirm}",
              len(tools) == 152 and digest == expected)
    direct = "yd_campaigns_get"
    metrika = "yd_metrika_counters_get"
    audience = "yd_audience_segments_get"
    wordstat = "yd_wordstat_regions_tree"
    inventory = "yd_direct_accounts_get"
    baseline = await server.list_tools()
    with (patch.multiple(server, TOKEN="agency-token", LOGIN="agency-default",
                         METRIKA_TOKEN="metrika-token", AUDIENCE_TOKEN="audience-token",
                         DIRECT_TOKENS={"legacy": "legacy-token", "demo": "old-demo-token"},
                         YANDEX_TOKENS={}, YANDEX_DEFAULT_LOGIN="",
                         READONLY=False, CONFIRM=True, ALLOWED_LOGINS=[]),
          patch.object(server, "_dispatch", new_callable=AsyncMock, return_value=[]) as dispatch):
        check("legacy-only config preserves the public tool list and schemas",
              await server.list_tools() == baseline)

        async def routes(label, name, args, expected):
            dispatch.reset_mock()
            await server.call_tool(name, args)
            call = dispatch.await_args
            auth = call.args[2] if call and len(call.args) == 3 else None
            check(label, dispatch.await_count == 1 and auth is not None and
                  (auth.token, auth.account_login, auth.direct_target_login,
                   auth.client_login_header) == expected and auth.token not in repr(auth) and
                  call.args[1] == {k: v for k, v in args.items() if k not in
                                   ("account_login", "client_login", "confirm")})

        async def denied(label, name, args, reason):
            dispatch.reset_mock()
            response = await server.call_tool(name, args)
            payload = json.loads(response[0].text) if response else {}
            check(label, not dispatch.called and payload.get("denied") is True and
                  reason in payload.get("reason", "") and "shared-demo-token" not in response[0].text)

        await routes("legacy-only client_login still selects its own token",
                     direct, {"client_login": "legacy"}, ("legacy-token", None, "legacy", ""))
        await denied("legacy-only config rejects the shared selector", direct,
                     {"account_login": "legacy"}, "account_login")

        with patch.object(server, "YANDEX_TOKENS", {"demo": "shared-demo-token", "other": "other-token"}):
            advertised = {t.name: t.inputSchema["properties"] for t in await server.list_tools()}
            for name in (direct, metrika, audience, "yd_audience_targets_add", wordstat, inventory):
                check(f"{name} advertises the selector only when supported",
                      ("account_login" in advertised[name]) == (name not in (wordstat, inventory)))
            await routes("shared login overrides duplicate legacy entry", direct,
                         {"account_login": "demo", "types": ["TEXT_CAMPAIGN"]},
                         ("shared-demo-token", "demo", "demo", ""))
            await routes("legacy map is a Direct-only fallback for the explicit shared selector",
                         direct, {"account_login": "legacy"}, ("legacy-token", "legacy", "legacy", ""))
            await routes("explicit legacy selector retains legacy map priority", direct,
                         {"client_login": "demo"}, ("old-demo-token", None, "demo", ""))
            await routes("unqualified Direct keeps the legacy default", direct, {},
                         ("agency-token", None, "agency-default", "agency-default"))
            for name, service_token in ((metrika, "metrika-token"), (audience, "audience-token")):
                await routes(f"{name} uses its existing default", name, {}, (service_token, None, "", ""))
                await routes(f"{name} selects a shared account", name, {"account_login": "demo"},
                             ("shared-demo-token", "demo", "", ""))
                await denied(f"{name} rejects a legacy-only account", name,
                             {"account_login": "legacy"}, "Unknown")
            for name in (wordstat, inventory):
                await denied(f"{name} rejects the shared selector", name,
                             {"account_login": "demo"}, "account_login")
            for invalid in (None, "", " ", "de\rmo", 123, "missing"):
                await denied(f"invalid account selector {invalid!r} is denied", direct,
                             {"account_login": invalid}, "account_login")
            await denied("Direct rejects both selectors even when they match", direct,
                         {"account_login": "demo", "client_login": "demo"}, "both")

            with patch.object(server, "YANDEX_DEFAULT_LOGIN", "demo"):
                for name in (direct, metrika, audience):
                    await routes(f"{name} uses the explicit shared default", name, {},
                                 ("shared-demo-token", "demo", "demo" if name == direct else "", ""))
                await routes("explicit agency subclient bypasses the shared default", direct,
                             {"client_login": "agency-client"},
                             ("agency-token", None, "agency-client", "agency-client"))
            with patch.object(server, "ALLOWED_LOGINS", ["other", "agency-default"]):
                await denied("Direct allowlist checks target even without a header", "yd_campaigns_add",
                             {"account_login": "demo", "confirm": True}, "YD_ALLOWED_LOGINS")
                await routes("Direct allowlist does not apply to Metrika", metrika,
                             {"account_login": "demo"}, ("shared-demo-token", "demo", "", ""))
            with patch.object(server, "READONLY", True):
                await denied("read-only wins over invalid routing on a mutation", "yd_campaigns_add",
                             {"account_login": "missing", "confirm": True}, "READ-ONLY")

            dispatch.reset_mock()
            response = await server.call_tool("yd_campaigns_add", {"account_login": "demo"})
            preview = json.loads(response[0].text) if response else {}
            check("confirm preview describes the selected target and never dispatches",
                  not dispatch.called and preview.get("confirm_required") is True and
                  preview.get("account_login") == "demo" and preview.get("direct_target_login") == "demo" and
                  preview.get("client_login", "") is None and "shared-demo-token" not in response[0].text)
            await routes("confirmed mutation dispatches the resolved account", "yd_campaigns_add",
                         {"account_login": "demo", "confirm": True},
                         ("shared-demo-token", "demo", "demo", ""))


asyncio.run(check_account_routing())


@asynccontextmanager
async def mock_http(handler):
    async_client = httpx.AsyncClient
    requests = []

    async def capture(request):
        requests.append(request)
        return await handler(request)

    transport = httpx.MockTransport(capture)
    with patch.object(httpx, "AsyncClient", side_effect=lambda **kwargs: async_client(transport=transport, **kwargs)):
        yield requests


async def check_http_routes():
    report_args = {"date_from": "2026-01-01", "date_to": "2026-01-02",
                   "field_names": ["Clicks"], "report_type": "CAMPAIGN_PERFORMANCE_REPORT",
                   "campaign_ids": [42]}
    conditions = [{"type": "exact", "url": "checkout"}]
    sample_csv = "email\nuser@example.test\n"
    status = 200

    async def respond(request):
        if status != 200:
            return httpx.Response(status, json={"error": {"error_detail": "echo transport-first"}})
        if request.url.path.endswith("/reports"):
            return httpx.Response(200, text="Clicks\n3\n")
        return httpx.Response(200, json={"result": {}})

    with (patch.multiple(server, YANDEX_TOKENS={"first": "transport-first", "second": "transport-second"},
                         YANDEX_DEFAULT_LOGIN="second", TOKEN="transport-legacy", LOGIN="agency",
                         DIRECT_TOKENS={}, ALLOWED_LOGINS=[], CONFIRM=True, READONLY=False,
                         API_URL="https://direct.example.test/custom/v5", USE_SANDBOX=False),
          patch.dict(server._extra_config, base_url="https://direct.example.test/custom/v5")):
        async with mock_http(respond) as requests:
            cases = [
                ("yd_campaigns_get", {"ids": [42]}, "POST", "/custom/v5/campaigns"),
                ("yd_ads_get", {"ad_ids": ["42"]}, "POST", "/custom/v501/ads"),
                ("yd_callouts_link", {"ad_id": 42, "callout_ids": [7]}, "POST", "/custom/v501/ads"),
                ("yd_report", report_args, "POST", "/custom/v5/reports"),
                ("yd_metrika_report", {"counter_id": 42, "metrics": ["ym:s:visits"]}, "GET", "/stat/v1/data"),
                ("yd_metrika_goal_create", {"counter_id": 42, "name": "Checkout", "goal_type": "url",
                                           "conditions": conditions}, "POST", "/management/v1/counter/42/goals"),
                ("yd_audience_segments_get", {"pixel": "sample-pixel"}, "GET", "/v1/management/segments"),
                ("yd_audience_segment_upload", {"content": sample_csv, "csv": True},
                 "POST", "/v1/management/segments/upload_csv_file"),
            ]
            for name, args, method, path in cases:
                requests.clear()
                response = await server.call_tool(name, dict(args, account_login="first", confirm=True))
                scheme = "Bearer" if server._is_direct(name) else "OAuth"
                check(f"{name} uses the selected credential at its HTTP endpoint",
                      len(requests) == 1 and requests[0].method == method and requests[0].url.path == path
                      and requests[0].headers.get("Authorization") == f"{scheme} transport-first"
                      and "Client-Login" not in requests[0].headers and not response[0].text.startswith("Error:"))
                if not requests:
                    continue
                request = requests[0]
                check(f"{name} keeps MCP controls off the wire",
                      all(key not in request.url.query and key not in request.content
                          for key in (b"account_login", b"client_login", b"confirm")))
                if name in ("yd_campaigns_get", "yd_ads_get"):
                    check(f"{name} preserves requested resource IDs",
                          json.loads(request.content)["params"]["SelectionCriteria"] == {"Ids": [42]})
                elif name == "yd_callouts_link":
                    check("extra v501 preserves the ad and callout IDs", json.loads(request.content) == {
                        "method": "update", "params": {"Ads": [{"Id": 42, "TextAd": {
                            "CalloutSetting": {"AdExtensions": [{"AdExtensionId": 7, "Operation": "SET"}]}}}]}})
                elif name == "yd_report":
                    criteria = json.loads(request.content)["params"]["SelectionCriteria"]
                    check("Reports preserves its campaign filter and processing headers",
                          criteria == {"DateFrom": "2026-01-01", "DateTo": "2026-01-02", "Filter": [
                              {"Field": "CampaignId", "Operator": "IN", "Values": ["42"]}]}
                          and request.headers.get("processingMode") == "auto"
                          and request.headers.get("returnMoneyInMicros") == "false")
                elif name == "yd_metrika_report":
                    check("Metrica report preserves the counter and metrics",
                          dict(request.url.params) == {"id": "42", "metrics": "ym:s:visits"})
                elif name == "yd_metrika_goal_create":
                    check("Metrica write preserves the goal body", json.loads(request.content) == {
                        "goal": {"name": "Checkout", "type": "url", "conditions": conditions}})
                elif name == "yd_audience_segments_get":
                    check("Audience read preserves the pixel filter", dict(request.url.params) == {"pixel": "sample-pixel"})
                elif name == "yd_audience_segment_upload":
                    check("Audience upload preserves the multipart data",
                          request.headers["Content-Type"].startswith("multipart/form-data;")
                          and b'name="file"; filename="segment_data.csv"' in request.content
                          and sample_csv.encode() in request.content)

            requests.clear()
            preview = await server.call_tool("yd_campaigns_add", {
                "account_login": "first", "campaigns": [{"transport-first": "nested value"}]})
            check("confirmation previews mask credential-shaped JSON keys",
                  not requests and "transport-first" not in preview[0].text
                  and json.loads(preview[0].text)["arguments"]["campaigns"] == [{"[redacted]": "nested value"}])

            for selector in ("", None):
                for mapped in (False, True):
                    requests.clear()
                    mapping = {"agency": "transport-mapped"} if mapped else {}
                    with patch.object(server, "DIRECT_TOKENS", mapping):
                        await server.call_tool("yd_campaigns_update", {
                            "client_login": selector, "confirm": True, "campaign_id": 42, "name": "Example"})
                    check(f"explicit {selector!r} legacy selector preserves {'mapped' if mapped else 'agency'} write route",
                          len(requests) == 1
                          and requests[0].headers.get("Authorization") ==
                          ("Bearer transport-mapped" if mapped else "Bearer transport-legacy")
                          and requests[0].headers.get("Client-Login") == (None if mapped else "agency")
                          and json.loads(requests[0].content)["method"] == "update")

                requests.clear()
                preview = json.loads((await server.call_tool("yd_campaigns_update", {
                    "client_login": selector, "campaign_id": 42}))[0].text)
                check(f"explicit {selector!r} legacy selector previews YD_LOGIN without HTTP",
                      not requests and preview.get("client_login") == "agency"
                      and "account_login" not in preview and preview.get("confirm_required") is True)
                with patch.object(server, "ALLOWED_LOGINS", ["second"]):
                    denied = json.loads((await server.call_tool("yd_campaigns_update", {
                        "client_login": selector, "campaign_id": 42, "confirm": True}))[0].text)
                check(f"explicit {selector!r} legacy selector gates the legacy target",
                      not requests and denied.get("denied") is True)

            requests.clear()
            await server.call_tool("yd_campaigns_update", {"campaign_id": 42, "confirm": True})
            check("omitted legacy selector still uses the shared default for a confirmed write",
                  len(requests) == 1 and requests[0].headers.get("Authorization") == "Bearer transport-second"
                  and "Client-Login" not in requests[0].headers)

            with patch.object(server, "USE_SANDBOX", True):
                requests.clear()
                await server.call_tool("yd_ads_get", {"client_login": "agency-client"})
                check("v501 Sandbox preserves legacy agency headers",
                      len(requests) == 1 and str(requests[0].url) == server.SANDBOX_URL + "01/ads"
                      and requests[0].headers.get("Authorization") == "Bearer transport-legacy"
                      and requests[0].headers.get("Client-Login") == "agency-client")

            log_stream = io.StringIO()
            handler = logging.StreamHandler(log_stream)
            server.logger.addHandler(handler)
            original_level = server.logger.level
            server.logger.setLevel(logging.DEBUG)
            try:
                with (patch.object(server, "LOG_BODIES", True),
                      patch("tools_direct_extra._LOG_BODIES", True)):
                    for status, (name, args, _, _) in zip((401, 403, 403, 403, 403),
                                                         (cases[0], cases[2], cases[3], cases[4], cases[-1])):
                        requests.clear()
                        response = await server.call_tool(name, dict(args, account_login="first", confirm=True))
                        check(f"{name} does not retry with another credential on denial",
                              len(requests) == 1 and requests[0].headers["Authorization"].endswith(" transport-first"))
                        check(f"{name} redacts echoed credentials in errors", "transport-first" not in response[0].text)
                debug_logs = log_stream.getvalue()
                log_stream.truncate(0)
                log_stream.seek(0)
                with patch.object(server, "LOG_BODIES", False):
                    await server.call_tool("yd_campaigns_get", {"account_login": "first"})
                check("exception logs exclude provider response details when body logging is disabled",
                      "failed" in log_stream.getvalue() and "echo" not in log_stream.getvalue())
            finally:
                server.logger.setLevel(original_level)
                server.logger.removeHandler(handler)
            check("HTTP errors and optional debug bodies redact echoed credentials",
                  "transport-first" not in debug_logs and "RESPONSE" in debug_logs)


async def check_overlapping_http_calls():
    both_started = asyncio.Event()
    arrivals = 0

    async def respond(request):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        if json.loads(request.content)["params"]["SelectionCriteria"]["Ids"] == [1]:
            raise httpx.ConnectError("synthetic connection failure", request=request)
        return httpx.Response(200, json={"result": {}})

    with patch.multiple(server, YANDEX_TOKENS={"first": "transport-first", "second": "transport-second"},
                        YANDEX_DEFAULT_LOGIN="second", ALLOWED_LOGINS=[]):
        async with mock_http(respond) as requests:
            async def first_then_default():
                response = await server.call_tool("yd_campaigns_get", {"account_login": "first", "ids": [1]})
                check("failed Direct call resets its request context", server.direct_auth_var.get() is None
                      and "synthetic connection failure" in response[0].text)
                await server.call_tool("yd_campaigns_get", {"ids": [3]})

            await asyncio.gather(first_then_default(), server.call_tool(
                "yd_campaigns_get", {"account_login": "second", "ids": [2]}))
            check("overlapping calls and the next default use their own credentials", {
                json.loads(r.content)["params"]["SelectionCriteria"]["Ids"][0]: r.headers["Authorization"]
                for r in requests} == {1: "Bearer transport-first", 2: "Bearer transport-second",
                                       3: "Bearer transport-second"} and len(requests) == 3)


async def check_wordstat_http_routes():
    async def respond(request):
        if request.url.host == "iam.api.cloud.yandex.net":
            return httpx.Response(200, json={"iamToken": "transport-iam"})
        return httpx.Response(200, json={"regions": []})

    for key in ("transport-cloud-key", ""):
        with (patch.multiple(server, TOKEN="transport-legacy", YC_FOLDER_ID="example-folder", YC_API_KEY=key,
                             YANDEX_TOKENS={"first": "transport-first"}, YANDEX_DEFAULT_LOGIN="first",
                             _iam_token_cache={"token": "", "expires": 0}),
              patch("tools_direct_extra._iam_cache", {"token": "", "expires": 0}),
              patch.dict(server._extra_config, token="transport-legacy", folder_id="example-folder", yc_api_key=key)):
            async with mock_http(respond) as requests:
                for name, endpoint in (("yd_wordstat_regions_tree", "getRegionsTree"),
                                       ("yd_wordstat_user_info", "userInfo")):
                    requests.clear()
                    for _ in range(2):
                        await server.call_tool(name, {"confirm": True})
                    auth = f"Api-Key {key}" if key else "Bearer transport-iam"
                    check(f"{name} uses {'API key' if key else 'legacy OAuth and cached IAM'}",
                          len(requests) == (2 if key else 3) and all(
                              r.method == "POST" and r.url.host == "searchapi.api.cloud.yandex.net"
                              and r.url.path == f"/v2/wordstat/{endpoint}" and r.headers.get("Authorization") == auth
                              and json.loads(r.content) == {"folderId": "example-folder"} for r in requests[-2:])
                          and (bool(key) or (requests[0].url.host == "iam.api.cloud.yandex.net"
                               and json.loads(requests[0].content) == {"yandexPassportOauthToken": "transport-legacy"})))


async def check_inventory_http_routes():
    async def respond(request):
        return httpx.Response(200, json={"error": {"error_detail": "echo transport-allowed"}})

    with patch.multiple(server, TOKEN="transport-primary", LOGIN="blocked-default",
                        DIRECT_TOKENS={"allowed": "transport-allowed", "blocked": "transport-blocked", "empty": ""},
                        YANDEX_TOKENS={"shared": "transport-shared"}, ALLOWED_LOGINS=["allowed", "empty"]):
        async with mock_http(respond) as requests:
            response = await server.call_tool("yd_direct_accounts_get", {})
            check("legacy inventory skips blocked, missing and shared credentials per probe",
                  [(r.headers.get("Authorization"), r.headers.get("Client-Login")) for r in requests]
                  == [("Bearer transport-allowed", None)])
            payload = json.loads(response[0].text)
            check("legacy inventory preserves its shape and redacts provider errors",
                  payload.get("default_login") == "blocked-default"
                  and [entry.get("configured_as") for entry in payload.get("accounts", [])] == ["allowed"]
                  and "transport-allowed" not in response[0].text)
            for login in ("", "allowed"):
                requests.clear()
                with patch.object(server, "LOGIN", login):
                    await server.call_tool("yd_direct_accounts_get", {})
                check("legacy inventory skips a primary token without an attributable header",
                      [r.headers.get("Authorization") for r in requests] == ["Bearer transport-allowed"])
            requests.clear()
            with patch.multiple(server, LOGIN="agency", ALLOWED_LOGINS=["agency"]):
                await server.call_tool("yd_direct_accounts_get", {})
            check("legacy inventory still probes an allowed agency route",
                  [(r.headers.get("Authorization"), r.headers.get("Client-Login")) for r in requests]
                  == [("Bearer transport-primary", "agency")])
            requests.clear()
            with patch.object(server, "TOKEN", ""):
                await server.call_tool("yd_direct_accounts_get", {})
            check("legacy inventory can probe an allowed mapped token without a primary token",
                  [r.headers.get("Authorization") for r in requests] == ["Bearer transport-allowed"])


asyncio.run(check_http_routes())
asyncio.run(check_overlapping_http_calls())
asyncio.run(check_wordstat_http_routes())
asyncio.run(check_inventory_http_routes())


async def check_service_discovery():
    name = "yd_yandex_accounts_get"
    not_checked = dict.fromkeys(("direct", "metrika", "audience"), "not_checked")
    verified = dict.fromkeys(not_checked, "read_verified")
    service_by_path = {"/custom/v5/clients": "direct", "/management/v1/counters": "metrika",
                       "/v1/management/segments": "audience"}
    endpoints = {"direct": ("POST", "direct.example.test", "Bearer"),
                 "metrika": ("GET", "api-metrika.yandex.net", "OAuth"),
                 "audience": ("GET", "api-audience.yandex.ru", "OAuth")}
    bodies = {"direct": {"result": {"Clients": [{"Login": "private-provider-payload"}]}},
              "metrika": {"counters": []}, "audience": {"segments": []}}
    overrides = {}
    stall = False
    cancelled = asyncio.Event()
    metrika_started = asyncio.Event()

    async def respond(request):
        service = service_by_path.get(request.url.path)
        if stall and service == "metrika":
            metrika_started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        if stall:
            await metrika_started.wait()
        if service in overrides:
            return overrides[service]
        return httpx.Response(200, json=bodies.get(service, {}))

    with patch.multiple(server, YANDEX_TOKENS={}, YANDEX_DEFAULT_LOGIN=""):
        check("discovery is hidden without the shared registry", name not in {t.name for t in await server.list_tools()})

    with patch.multiple(server, YANDEX_TOKENS={"first": "discovery-first", "second": "discovery-second"},
                        YANDEX_DEFAULT_LOGIN="first", ALLOWED_LOGINS=[], READONLY=True,
                        API_URL="https://direct.example.test/custom/v5", USE_SANDBOX=False):
        async with mock_http(respond) as requests:
            tools = {tool.name: tool.inputSchema["properties"] for tool in await server.list_tools()}
            check("discovery is read-only, non-Direct and advertises only its own controls",
                  not server._is_mutating(name) and not server._is_direct(name)
                  and set(tools.get(name, {})) == {"account_login", "verify"})

            async def invoke(**args):
                result = await asyncio.wait_for(server.call_tool(name, args), timeout=1)
                check("discovery output excludes tokens and provider payloads",
                      all(secret not in result[0].text for secret in
                          ("discovery-first", "discovery-second", "private-provider-payload")))
                return json.loads(result[0].text)

            def states(payload):
                accounts = payload.get("accounts", [])
                return accounts[0]["services"] if accounts else {}

            filtered = await invoke(account_login="second")
            check("discovery preserves the selected filter without HTTP", not requests and filtered == {
                "default_login": "first", "accounts": [{"account_login": "second", "services": not_checked}]})
            listing = await invoke()
            check("unfiltered discovery lists both accounts without HTTP", not requests and listing == {
                "default_login": "first", "accounts": [
                    {"account_login": login, "services": not_checked} for login in ("first", "second")]})
            for args in ({"verify": True}, {"account_login": "unknown", "verify": True},
                         {"account_login": None}, {"account_login": []}, {"verify": "true"}):
                rejected = await invoke(**args)
                check("invalid discovery arguments fail before HTTP", not requests and rejected.get("denied") is True)

            confirmed = await invoke(account_login="second", verify=True)
            check("discovery treats successful empty lists as verified", states(confirmed) == verified
                  and [row["account_login"] for row in confirmed.get("accounts", [])] == ["second"])
            check("discovery verifies only the selected token, with no agency header or MCP controls",
                  len(requests) == 3 and {r.url.path for r in requests} == set(service_by_path) and all(
                      (r.method, r.url.host, r.headers.get("Authorization", "").partition(" ")[0])
                      == endpoints[service_by_path[r.url.path]]
                      and r.headers.get("Authorization", "").partition(" ")[2] == "discovery-second"
                      and "Client-Login" not in r.headers and not r.url.query and all(
                          value is not None and value > 0 for value in r.extensions["timeout"].values())
                      for r in requests))
            direct_request = next((r for r in requests if r.url.path == "/custom/v5/clients"), None)
            check("discovery sends a minimal Direct Clients.get to the configured endpoint", direct_request is not None
                  and direct_request.method == "POST" and direct_request.url.host == "direct.example.test"
                  and json.loads(direct_request.content) == {"method": "get", "params": {"FieldNames": ["Login"]}})

            requests.clear()
            overrides.update(direct=httpx.Response(200, json={"error": {"error_code": 53,
                             "error_detail": "private-provider-payload discovery-second"}}),
                             metrika=httpx.Response(401), audience=httpx.Response(200, text="not JSON"))
            check("discovery distinguishes provider denial from malformed responses",
                  states(await invoke(account_login="second", verify=True)) == {
                      "direct": "access_denied", "metrika": "access_denied", "audience": "unavailable"}
                  and len(requests) == 3)

            for status in (429, 503, 302):
                requests.clear()
                overrides.clear()
                overrides["direct"] = httpx.Response(status, headers={"Retry-After": "3600",
                                                                      "Location": "https://other.example.test/"})
                check(f"discovery does one attempt and preserves other services after HTTP {status}",
                      states(await invoke(account_login="second", verify=True)) == dict(verified, direct="unavailable")
                      and len(requests) == 3 and sum(r.url.path == "/custom/v5/clients" for r in requests) == 1)

            requests.clear()
            overrides.clear()
            overrides["direct"] = httpx.Response(200, json={"error": {"error_code": 52}})
            overrides["metrika"] = httpx.Response(200, json={"counters": [None]})
            overrides["audience"] = httpx.Response(403, json={"error": "access_denied"})
            check("discovery distinguishes transient Direct errors, invalid list items and HTTP 403",
                  states(await invoke(account_login="second", verify=True)) == {
                      "direct": "unavailable", "metrika": "unavailable", "audience": "access_denied"}
                  and len(requests) == 3)

            requests.clear()
            overrides.clear()
            with patch.object(server, "ALLOWED_LOGINS", ["first"]):
                check("discovery skips only the allowlist-blocked Direct probe",
                      states(await invoke(account_login="second", verify=True)) == dict(verified, direct="not_checked")
                      and len(requests) == 2 and all(r.method == "GET" for r in requests))

            for clients in ([{}], [{"Login": None}], [{"Login": 42}], [{"Login": ""}],
                            [{"Login": " "}], [{"Login": "valid"}, {}], []):
                requests.clear()
                overrides["direct"] = httpx.Response(200, json={"result": {"Clients": clients}})
                expected = "unavailable" if clients else "read_verified"
                check(f"Direct discovery validates requested Login for {clients!r}",
                      states(await invoke(account_login="second", verify=True)) == dict(verified, direct=expected)
                      and len(requests) == 3)
            overrides.clear()

            requests.clear()
            stall = True
            with patch.object(server, "_DISCOVERY_TIMEOUT", 0.05, create=True):
                check("shared discovery deadline drains a stalled service and retains completed probes",
                      states(await invoke(account_login="second", verify=True)) == dict(verified, metrika="unavailable")
                      and len(requests) == 3 and cancelled.is_set())


asyncio.run(check_service_discovery())

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASSED")
