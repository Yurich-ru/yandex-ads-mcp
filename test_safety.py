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
import json
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
    "yd_ads_update", "yd_keyword_bids_set_auto", "yd_bid_modifiers_add",
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
    "yd_metrika_confirm_user_params",
]
# Explicit allowlist of read-only tools: a new read-only tool must be added here,
# otherwise the exhaustive check below fails instead of silently blocking reads.
READONLY = [
    "yd_ad_extensions_get", "yd_ad_images_get", "yd_adgroups_get", "yd_ads_get",
    "yd_audience_accounts_get", "yd_audience_delegates_get", "yd_audience_grants_get",
    "yd_audience_pixels_get", "yd_audience_segments_get", "yd_audience_targets_get",
    "yd_bid_modifiers_get", "yd_businesses_get", "yd_campaigns_get", "yd_changes_check",
    "yd_changes_timestamp_get", "yd_clients_get", "yd_creatives_get", "yd_dictionaries",
    "yd_direct_accounts_get", "yd_excluded_sites_get", "yd_feeds_get", "yd_interests_get",
    "yd_keyword_bids_get", "yd_keywords_get", "yd_keywords_has_volume", "yd_keywords_research",
    "yd_metrika_annotations_get", "yd_metrika_conversions_status", "yd_metrika_counter_get",
    "yd_metrika_counters_get", "yd_metrika_delegates_get", "yd_metrika_filters_get",
    "yd_metrika_goals_get", "yd_metrika_grants_get", "yd_metrika_labels_get",
    "yd_metrika_report", "yd_metrika_report_by_time", "yd_metrika_report_comparison",
    "yd_metrika_report_drilldown", "yd_metrika_segments_get",
    "yd_negative_keywords_sets_get", "yd_regions_get", "yd_report",
    "yd_retargeting_lists_get", "yd_sitelinks_get", "yd_smart_targets_get", "yd_vcards_get",
    "yd_videos_get", "yd_wordstat_dynamics", "yd_wordstat_regions",
    "yd_wordstat_regions_tree", "yd_wordstat_top_requests", "yd_wordstat_user_info",
]
all_tools = server.TOOLS + server.METRIKA_TOOLS + server.EXTRA_DIRECT_TOOLS + server.AUDIENCE_TOOLS
all_names = {tool.name for tool in all_tools}
for n in MUTATING:
    check(f"{n} is mutating", server._is_mutating(n) is True)
for n in READONLY:
    check(f"{n} is read-only", server._is_mutating(n) is False)
check("read-only allowlist has no unknown tools", set(READONLY) <= all_names)
check("every tool is classified escaping the read-only allowlist",
      {n for n in all_names if not server._is_mutating(n)} == set(READONLY))
check("removed non-existent bidmodifiers.toggle tool", "yd_bid_modifiers_toggle" not in all_names)

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
check("clean result has no _partial_success",
      "_partial_success" not in ok and "_partial_success" not in ok["result"])
bad = annotate_partial({"result": {"AddResults": [
    {"Id": 1},
    {"Errors": [{"Code": 5, "Message": "Bad text"}]},
    {"Warnings": [{"Code": 9, "Message": "Truncated"}]},
]}})
ps = bad.get("result", {}).get("_partial_success", {})
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

print("== partial success survives handlers ==")
async def partial_api(client, service, method, params):
    return annotate_partial({"result": {"AddResults": [
        {"Id": 1},
        {"Errors": [{"Code": 5, "Message": "Bad text"}]},
    ]}})

original_api = server._api
server._api = partial_api
try:
    out = asyncio.run(server._handle_campaigns_add(None, {"name": "n", "start_date": "2026-01-01"}))
    partial_payload = json.loads(out[0].text)
    partial = partial_payload.get("_partial_success", {})
    check("handler keeps error count", partial.get("error_count") == 1)
    check("handler keeps ok=false", partial.get("ok") is False)
finally:
    server._api = original_api

print("== Direct payload contracts ==")
payload_calls = []


async def capture_api(client, service, method, params):
    payload_calls.append((service, method, params))
    return {"result": {}}


original_api = server._api
server._api = capture_api
try:
    asyncio.run(server._handle_bids_set(None, {"bids": [
        {"keyword_id": 1, "search_bid": 1.5, "network_bid": 0.5},
    ]}))
    bids_item = payload_calls[-1][2]["Bids"][0]
    check("bids set uses Bid/ContextBid",
          bids_item.get("Bid") == 1500000 and bids_item.get("ContextBid") == 500000 and
          "SearchBid" not in bids_item and "NetworkBid" not in bids_item)

    asyncio.run(server._handle_keyword_bids_get(None, {"keyword_ids": [1]}))
    kb_params = payload_calls[-1][2]
    check("keyword bids get uses top-level fields",
          kb_params["FieldNames"] == ["KeywordId", "AdGroupId", "CampaignId"])
    check("keyword bids get uses SearchFieldNames/NetworkFieldNames",
          kb_params["SearchFieldNames"] == ["Bid", "AutotargetingSearchBidIsAuto"] and
          kb_params["NetworkFieldNames"] == ["Bid"])

    asyncio.run(server._handle_keyword_bids_set_auto(None, {"bids": [{
        "keyword_id": 1,
        "target_traffic_volume": 50,
        "increase_percent": 0,
        "bid_ceiling": 2.5,
    }]}))
    auto_item = payload_calls[-1][2]["KeywordBids"][0]
    auto_rule = auto_item.get("BiddingRule", {}).get("SearchByTrafficVolume", {})
    check("setAuto uses BiddingRule/SearchByTrafficVolume",
          auto_rule.get("TargetTrafficVolume") == 50 and
          auto_rule.get("IncreasePercent") == 0 and
          auto_rule.get("BidCeiling") == 2500000)
    check("setAuto does not send legacy SearchAutoStrategy", "SearchAutoStrategy" not in auto_item)

    asyncio.run(server._handle_bid_modifiers_add(None, {
        "campaign_id": 1, "mobile_adjustment": 0,
    }))
    modifier_items = payload_calls[-1][2]["BidModifiers"][0]
    check("bid modifiers keep zero adjustment",
          modifier_items.get("MobileAdjustment") == {"BidModifier": 0})

    asyncio.run(server._handle_campaigns_update(None, {
        "campaign_id": 1, "negative_keywords": [],
    }))
    check("campaign update can clear negative keywords",
          payload_calls[-1][2]["Campaigns"][0].get("NegativeKeywords") == {"Items": []})

    asyncio.run(server._handle_ad_extensions_get(None, {"ids": [1]}))
    ext_params = payload_calls[-1][2]
    check("ad extensions get uses CalloutFieldNames",
          ext_params["FieldNames"] == ["Id", "Type", "Status"] and
          ext_params["CalloutFieldNames"] == ["CalloutText"])

    asyncio.run(server._handle_retargeting_lists_add(None, {
        "name": "n",
        "rules": [{"operator": "ANY", "goals": [{"audience_segment_id": 555}]}],
    }))
    rule_arg = payload_calls[-1][2]["RetargetingLists"][0]["Rules"][0]["Arguments"][0]
    check("retargeting add encodes Audience segment", rule_arg.get("ExternalId") == 2000000555)

    missing_goal = False
    try:
        asyncio.run(server._handle_retargeting_lists_add(None, {
            "name": "n",
            "rules": [{"operator": "ANY", "goals": [{}]}],
        }))
    except ValueError:
        missing_goal = True
    check("retargeting add rejects goal without id", missing_goal)

    both_targets = False
    try:
        asyncio.run(server._handle_audience_targets_add(None, {"targets": [{
            "ad_group_id": 1, "retargeting_list_id": 2, "interest_id": 3,
        }]}))
    except ValueError:
        both_targets = True
    check("audience target rejects both list and interest", both_targets)
finally:
    server._api = original_api

print("== extra Direct payload contracts ==")
import tools_direct_extra as tde  # noqa: E402

extra_calls = []


async def capture_extra_api(client, service, method, params, **kwargs):
    extra_calls.append((service, method, params))
    return {"result": {}}


original_extra_api = tde._api
tde._api = capture_extra_api
try:
    asyncio.run(server._extra_dispatch["yd_creatives_get"](
        None, {"creative_ids": [1]}, server._extra_config))
    creative_params = extra_calls[-1][2]
    check("creatives get uses type-specific field names",
          creative_params["FieldNames"] == ["Id", "Type", "Name", "PreviewUrl"] and
          creative_params["VideoExtensionCreativeFieldNames"] == ["Duration"])

    asyncio.run(server._extra_dispatch["yd_feeds_get"](
        None, {"feed_ids": [1]}, server._extra_config))
    feed_params = extra_calls[-1][2]
    check("feeds get keeps UrlFeed out of FieldNames",
          "UrlFeed" not in feed_params["FieldNames"] and
          feed_params["UrlFeedFieldNames"] == ["Url", "Login", "RemoveUtmTags"])

    asyncio.run(server._extra_dispatch["yd_videos_get"](
        None, {"video_ids": ["abc"]}, server._extra_config))
    video_params = extra_calls[-1][2]
    check("videos get requests only Id/Status and string IDs",
          video_params["FieldNames"] == ["Id", "Status"] and
          video_params["SelectionCriteria"]["Ids"] == ["abc"])

    asyncio.run(server._extra_dispatch["yd_retargeting_lists_update"](
        None,
        {"id": 1, "rules": [{"operator": "ALL", "goals": [{"audience_segment_id": 777}]}]},
        server._extra_config))
    update_arg = extra_calls[-1][2]["RetargetingLists"][0]["Rules"][0]
    check("retargeting update uses ALL operator and encodes segment",
          update_arg["Operator"] == "ALL" and
          update_arg["Arguments"][0]["ExternalId"] == 2000000777 and
          "MembershipLifeSpan" not in update_arg["Arguments"][0])
finally:
    tde._api = original_extra_api

print("== Metrika payload contracts ==")
import tools_metrika as tm  # noqa: E402

metrika_calls = []


async def capture_metrika_api(client, method, path, token, params=None, body=None, files=None):
    metrika_calls.append({"method": method, "path": path, "params": params, "body": body, "files": files})
    return {}


original_metrika_api = tm._metrika_api
tm._metrika_api = capture_metrika_api
try:
    asyncio.run(server._metrika_dispatch["yd_metrika_counter_update"](
        None, {"counter_id": 1, "site": "example.com"}))
    check("counter update uses site2",
          metrika_calls[-1]["body"] == {"counter": {"site2": {"site": "example.com"}}})

    asyncio.run(server._metrika_dispatch["yd_metrika_upload_conversions"](
        None, {"counter_id": 1, "conversions": [
            {"Target": "order", "DateTime": 1700000000, "ClientId": "123"},
        ]}))
    conversion_call = metrika_calls[-1]
    conversion_csv = conversion_call["files"]["file"][1].decode()
    check("conversions upload posts multipart CSV",
          conversion_call["path"].endswith("/offline_conversions/upload") and
          conversion_csv.splitlines()[0] == "Target,DateTime,ClientId")
    check("conversions upload includes the row", "order,1700000000,123" in conversion_csv)

    asyncio.run(server._metrika_dispatch["yd_metrika_upload_calls"](
        None, {"counter_id": 1, "calls": [
            {"ClientId": "123", "DateTime": 1700000000, "PhoneNumber": "+70123"},
        ]}))
    check("calls upload uses the documented endpoint",
          metrika_calls[-1]["path"].endswith("/offline_conversions/upload_calls"))

    asyncio.run(server._metrika_dispatch["yd_metrika_upload_expenses"](
        None, {"counter_id": 1, "expenses": [
            {"Date": "2026-01-01", "UTMSource": "google", "Expenses": 100},
        ]}))
    check("expenses upload posts multipart CSV",
          metrika_calls[-1]["path"].endswith("/expense/upload") and
          metrika_calls[-1]["files"]["file"][1].decode().splitlines()[0] == "Date,UTMSource,Expenses")

    asyncio.run(server._metrika_dispatch["yd_metrika_upload_user_params"](
        None, {"counter_id": 1, "action": "update", "users": [
            {"ClientId": "123", "key1": "v1"},
        ]}))
    user_call = metrika_calls[-1]
    check("user params upload uses the documented endpoint and action",
          user_call["path"].endswith("/user_params/uploadings/upload") and
          user_call["params"] == {"action": "update"})

    asyncio.run(server._metrika_dispatch["yd_metrika_confirm_user_params"](
        None, {"counter_id": 1, "uploading_id": 42, "action": "update"}))
    check("user params confirm posts uploading id and action",
          metrika_calls[-1]["path"].endswith("/user_params/uploading/42/confirm") and
          metrika_calls[-1]["body"] == {"uploading": {"id": 42, "action": "update"}})
finally:
    tm._metrika_api = original_metrika_api

print("== confirm preview redaction ==")
preview_text = server._confirm_preview("yd_test", {
    "password": "s3cret",
    "image_data": "A" * 1000,
    "note": "n" * 600,
    "images": [{"name": "hero", "image_data": "B" * 1000}],
}, "")[0].text
preview = json.loads(preview_text)
check("preview redacts password", "s3cret" not in preview_text and
      str(preview["arguments"]["password"]).startswith("<omitted"))
check("preview redacts binary payloads", "A" * 1000 not in preview_text)
check("preview redacts nested payloads", "B" * 1000 not in preview_text)
check("preview truncates long strings", len(preview["arguments"]["note"]) < 600)

print("== accounts probe respects allowlist ==")
probe_calls = []


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


async def fake_probe(client, url, *, method="POST", headers=None, json_body=None,
                     params=None, files=None, timeout, max_attempts=4):
    probe_calls.append(headers["Authorization"])
    return FakeResponse({"result": {"Clients": [{"Login": "probed"}]}})


original_probe = server.request_with_retry
original_tokens = server.DIRECT_TOKENS
original_allowed = server.ALLOWED_LOGINS
server.request_with_retry = fake_probe
server.DIRECT_TOKENS = {"allowed": "token-a", "blocked": "token-b"}
server.ALLOWED_LOGINS = ["allowed"]
try:
    accounts_text = asyncio.run(server._handle_direct_accounts_get(None, {}))[0].text
    accounts = json.loads(accounts_text)["accounts"]
    configured = {entry["configured_as"] for entry in accounts}
    check("accounts probe skips non-whitelisted token",
          "YD_DIRECT_TOKENS:blocked" not in configured)
    check("accounts probe includes whitelisted token",
          "YD_DIRECT_TOKENS:allowed" in configured)
finally:
    server.request_with_retry = original_probe
    server.DIRECT_TOKENS = original_tokens
    server.ALLOWED_LOGINS = original_allowed

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASSED")
