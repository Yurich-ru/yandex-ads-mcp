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

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASSED")
