"""Extra Yandex Direct API tools: VCards, Feeds, Smart targets, Ad types, Videos, Creatives, misc."""

import re
import json
import time
import base64
import asyncio
import logging
import datetime
import contextvars

from mcp.types import Tool, TextContent

logger = logging.getLogger("yandex-ads-mcp")

# Transient HTTP statuses worth retrying with backoff.
_RETRY_STATUS = (429, 500, 502, 503, 504)


async def request_with_retry(client, url, *, headers, json_body, timeout, max_attempts=4):
    """POST with capped exponential backoff on transient errors / rate limits.

    Honours Retry-After when present. Returns the final response (success or last failure).
    """
    delay = 1.0
    resp = None
    for attempt in range(1, max_attempts + 1):
        resp = await client.post(url, headers=headers, json=json_body, timeout=timeout)
        if resp.status_code in _RETRY_STATUS and attempt < max_attempts:
            ra = resp.headers.get("Retry-After", "")
            wait = float(ra) if ra.isdigit() else delay
            logger.warning("HTTP %s from %s — retry %d/%d in %.1fs",
                           resp.status_code, url, attempt, max_attempts - 1, wait)
            await asyncio.sleep(wait)
            delay = min(delay * 2, 30.0)
            continue
        break
    return resp


def log_units(resp):
    """Log Yandex Direct API points from the Units header (format: spent/balance/limit)."""
    u = resp.headers.get("Units")
    if not u:
        return
    try:
        spent, balance, limit = (int(x) for x in u.split("/"))
        level = logging.WARNING if (limit and balance < limit * 0.05) else logging.INFO
        logger.log(level, "Direct API units: balance %s/%s (spent %s on this call)", balance, limit, spent)
    except Exception:
        logger.info("Direct API units: %s", u)


def iam_expiry(data, now):
    """Compute IAM token expiry epoch from the API's expiresAt, with an 11h fallback."""
    exp = data.get("expiresAt")
    if exp:
        try:
            s = exp.replace("Z", "+00:00")
            s = re.sub(r"(\.\d{6})\d+", r"\1", s)  # trim sub-microsecond precision
            return datetime.datetime.fromisoformat(s).timestamp() - 60
        except Exception:
            logger.debug("Could not parse IAM expiresAt=%r, using 11h fallback", exp)
    return now + 11 * 3600

# Per-call Client-Login (agency multi-account). Set by the dispatcher in server.py.
client_login_var = contextvars.ContextVar("client_login", default="")

# Toggled from server.py via set_log_bodies(); when False, request/response bodies are not logged.
_LOG_BODIES = False


def set_log_bodies(enabled: bool):
    global _LOG_BODIES
    _LOG_BODIES = bool(enabled)


def _log_body(prefix, *args):
    if _LOG_BODIES:
        logger.debug(prefix, *args)


def annotate_partial(data):
    """Surface Yandex Direct per-item Errors/Warnings (partial success) at the top level.

    Direct v5 returns 200 OK even when individual items fail; the failures live inside
    result.<List>[i].Errors / .Warnings. Without this, a half-failed bulk add looks like success.
    """
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return data
    issues = []
    for key, val in result.items():
        if not isinstance(val, list):
            continue
        for idx, item in enumerate(val):
            if not isinstance(item, dict):
                continue
            if item.get("Errors"):
                issues.append({"list": key, "index": idx, "level": "error", "messages": item["Errors"]})
            if item.get("Warnings"):
                issues.append({"list": key, "index": idx, "level": "warning", "messages": item["Warnings"]})
    if issues:
        error_count = sum(1 for i in issues if i["level"] == "error")
        data = dict(data)
        data["_partial_success"] = {
            "ok": error_count == 0,
            "error_count": error_count,
            "warning_count": len(issues) - error_count,
            "issues": issues,
        }
    return data


# ── Helpers ───────────────────────────────────────────────────────────

def _result(data):
    return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]


async def _api(client, service, method, params, *, base_url, token, login="", timeout=120, **_kwargs):
    url = f"{base_url}/{service}"
    body = {"method": method, "params": params}
    headers = {"Authorization": f"Bearer {token}", "Accept-Language": "ru", "Content-Type": "application/json"}
    eff_login = client_login_var.get("") or login
    if eff_login:
        headers["Client-Login"] = eff_login
    _log_body("EXTRA REQUEST %s %s: %s", url, method, json.dumps(body, ensure_ascii=False)[:2000])
    resp = await request_with_retry(client, url, headers=headers, json_body=body, timeout=timeout)
    log_units(resp)
    data = resp.json()
    _log_body("EXTRA RESPONSE %s: %s", resp.status_code, json.dumps(data, ensure_ascii=False)[:2000])
    if "error" in data:
        raise Exception(f"API error {data['error'].get('error_code')}: {data['error'].get('error_detail', data['error'].get('error_string'))}")
    return annotate_partial(data)


async def _api501(client, service, method, params, *, base_url, token, login="", timeout=120, **_kwargs):
    """Call via v501 endpoint (for shopping ads, callout linking, etc.)."""
    url = base_url.replace("/v5", "/v501") + f"/{service}"
    body = {"method": method, "params": params}
    headers = {"Authorization": f"Bearer {token}", "Accept-Language": "ru", "Content-Type": "application/json"}
    eff_login = client_login_var.get("") or login
    if eff_login:
        headers["Client-Login"] = eff_login
    _log_body("EXTRA v501 REQUEST %s %s: %s", url, method, json.dumps(body, ensure_ascii=False)[:2000])
    resp = await request_with_retry(client, url, headers=headers, json_body=body, timeout=timeout)
    log_units(resp)
    data = resp.json()
    _log_body("EXTRA v501 RESPONSE %s: %s", resp.status_code, json.dumps(data, ensure_ascii=False)[:2000])
    if "error" in data:
        raise Exception(f"API error {data['error'].get('error_code')}: {data['error'].get('error_detail', data['error'].get('error_string'))}")
    return annotate_partial(data)


# IAM token cache (shared)
_iam_cache = {"token": "", "expires": 0}


async def _get_iam(client, oauth_token):
    now = time.time()
    if _iam_cache["token"] and _iam_cache["expires"] > now + 300:
        return _iam_cache["token"]
    resp = await client.post(
        "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        json={"yandexPassportOauthToken": oauth_token}, timeout=30,
    )
    data = resp.json()
    if "iamToken" not in data:
        raise Exception(f"Failed to get IAM token: {data}")
    _iam_cache["token"] = data["iamToken"]
    _iam_cache["expires"] = iam_expiry(data, now)
    return _iam_cache["token"]


# ── Tool definitions ──────────────────────────────────────────────────

EXTRA_DIRECT_TOOLS = [
    # --- VCards ---
    Tool(
        name="yd_vcards_add",
        description="Add a VCard (business card) to a campaign.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
                "company": {"type": "string", "description": "Company name"},
                "city_code": {"type": "string", "description": "Phone city code (e.g. '495')"},
                "phone_number": {"type": "string", "description": "Phone number (e.g. '1234567')"},
                "city": {"type": "string", "description": "City name"},
                "country": {"type": "string", "description": "Country name"},
                "street": {"type": "string"},
                "house": {"type": "string"},
                "work_time": {"type": "string", "description": "Working hours, e.g. '0;6;10;00;18;00'"},
                "extra_message": {"type": "string", "description": "Additional message"},
            },
            "required": ["campaign_id", "company", "city_code", "phone_number", "city", "country"],
        },
    ),
    Tool(
        name="yd_vcards_get",
        description="Get VCards. Optionally filter by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "vcard_ids": {"type": "array", "items": {"type": "integer"}, "description": "VCard IDs to get"},
            },
        },
    ),
    Tool(
        name="yd_vcards_delete",
        description="Delete VCards by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "vcard_ids": {"type": "array", "items": {"type": "integer"}, "description": "VCard IDs to delete"},
            },
            "required": ["vcard_ids"],
        },
    ),
    # --- Feeds ---
    Tool(
        name="yd_feeds_add",
        description="Add a feed for dynamic/smart/shopping ads.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Feed name"},
                "business_type": {"type": "string", "enum": ["RETAIL", "HOTELS", "REALTY", "AUTOMOBILES", "FLIGHTS", "OTHER"]},
                "url": {"type": "string", "description": "Feed URL"},
                "login": {"type": "string", "description": "HTTP auth login (optional)"},
                "password": {"type": "string", "description": "HTTP auth password (optional)"},
                "remove_utm_tags": {"type": "string", "enum": ["YES", "NO"], "description": "Remove UTM from URLs"},
            },
            "required": ["name", "business_type", "url"],
        },
    ),
    Tool(
        name="yd_feeds_get",
        description="Get feeds. Optionally filter by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "feed_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_feeds_update",
        description="Update a feed (name, URL, auth).",
        inputSchema={
            "type": "object",
            "properties": {
                "feed_id": {"type": "integer", "description": "Feed ID"},
                "name": {"type": "string"},
                "url": {"type": "string"},
                "login": {"type": "string"},
                "password": {"type": "string"},
            },
            "required": ["feed_id"],
        },
    ),
    Tool(
        name="yd_feeds_delete",
        description="Delete feeds by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "feed_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["feed_ids"],
        },
    ),
    # --- Smart Ad Targets ---
    Tool(
        name="yd_smart_targets_add",
        description="Add a smart ad target (filter) to an ad group.",
        inputSchema={
            "type": "object",
            "properties": {
                "adgroup_id": {"type": "integer", "description": "Ad group ID"},
                "name": {"type": "string", "description": "Target name"},
                "available_items_only": {"type": "string", "enum": ["YES", "NO"], "description": "Show only available items"},
                "conditions": {"type": "array", "items": {"type": "object"}, "description": "Filter conditions array"},
            },
            "required": ["adgroup_id", "name"],
        },
    ),
    Tool(
        name="yd_smart_targets_get",
        description="Get smart ad targets by campaign, ad group, or target IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_ids": {"type": "array", "items": {"type": "integer"}},
                "adgroup_ids": {"type": "array", "items": {"type": "integer"}},
                "target_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_smart_targets_action",
        description="Suspend, resume, or delete smart ad targets.",
        inputSchema={
            "type": "object",
            "properties": {
                "target_ids": {"type": "array", "items": {"type": "integer"}},
                "action": {"type": "string", "enum": ["suspend", "resume", "delete"]},
            },
            "required": ["target_ids", "action"],
        },
    ),
    # --- Ad type tools ---
    Tool(
        name="yd_ads_add_dynamic",
        description="Create a dynamic text ad.",
        inputSchema={
            "type": "object",
            "properties": {
                "adgroup_id": {"type": "integer", "description": "Ad group ID"},
                "text": {"type": "string", "description": "Ad text (max 81 chars)"},
                "ad_image_hash": {"type": "string"},
                "sitelink_set_id": {"type": "integer"},
            },
            "required": ["adgroup_id", "text"],
        },
    ),
    Tool(
        name="yd_ads_add_image",
        description="Create an image ad (TextImageAd).",
        inputSchema={
            "type": "object",
            "properties": {
                "adgroup_id": {"type": "integer", "description": "Ad group ID"},
                "ad_image_hash": {"type": "string", "description": "Image hash"},
                "href": {"type": "string", "description": "Landing page URL"},
            },
            "required": ["adgroup_id", "ad_image_hash", "href"],
        },
    ),
    Tool(
        name="yd_ads_add_shopping",
        description="Create a shopping ad (uses v501 API).",
        inputSchema={
            "type": "object",
            "properties": {
                "adgroup_id": {"type": "integer", "description": "Ad group ID"},
                "feed_id": {"type": "integer", "description": "Feed ID"},
                "conditions": {"type": "array", "items": {"type": "object"}, "description": "Feed filter conditions"},
                "default_texts": {"type": "object", "description": "Default texts for the ad"},
                "sitelink_set_id": {"type": "integer"},
            },
            "required": ["adgroup_id", "feed_id"],
        },
    ),
    # --- Video / Creative tools ---
    Tool(
        name="yd_videos_upload",
        description="Upload a video from a local file (base64-encoded).",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to video file on server"},
                "name": {"type": "string", "description": "Video name (optional)"},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="yd_videos_get",
        description="Get ad videos. Optionally filter by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "video_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_creatives_add",
        description="Create a video extension creative.",
        inputSchema={
            "type": "object",
            "properties": {
                "video_id": {"type": "string", "description": "Video ID"},
            },
            "required": ["video_id"],
        },
    ),
    Tool(
        name="yd_creatives_get",
        description="Get creatives. Optionally filter by IDs or types.",
        inputSchema={
            "type": "object",
            "properties": {
                "creative_ids": {"type": "array", "items": {"type": "integer"}},
                "types": {"type": "array", "items": {"type": "string"}, "description": "Creative types filter"},
            },
        },
    ),
    # --- Misc tools ---
    Tool(
        name="yd_callouts_link",
        description="Link callout extensions to an ad (uses v501 API).",
        inputSchema={
            "type": "object",
            "properties": {
                "ad_id": {"type": "integer", "description": "Ad ID"},
                "callout_ids": {"type": "array", "items": {"type": "integer"}, "description": "Callout extension IDs"},
            },
            "required": ["ad_id", "callout_ids"],
        },
    ),
    Tool(
        name="yd_bid_modifiers_toggle",
        description="Enable or disable bid modifiers.",
        inputSchema={
            "type": "object",
            "properties": {
                "bid_modifier_ids": {"type": "array", "items": {"type": "integer"}},
                "enabled": {"type": "boolean", "description": "true = enable, false = disable"},
            },
            "required": ["bid_modifier_ids", "enabled"],
        },
    ),
    Tool(
        name="yd_adgroups_update",
        description="Update an ad group (name, regions, negatives, tracking).",
        inputSchema={
            "type": "object",
            "properties": {
                "adgroup_id": {"type": "integer", "description": "Ad group ID"},
                "name": {"type": "string"},
                "region_ids": {"type": "array", "items": {"type": "integer"}},
                "negative_keywords": {"type": "array", "items": {"type": "string"}},
                "tracking_params": {"type": "string", "description": "UTM tracking params"},
            },
            "required": ["adgroup_id"],
        },
    ),
    Tool(
        name="yd_regions_get",
        description="Get regions dictionary (convenience wrapper).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="yd_interests_get",
        description="Get interests dictionary (convenience wrapper).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="yd_wordstat_user_info",
        description="Get Wordstat API quota info (uses IAM token).",
        inputSchema={"type": "object", "properties": {}},
    ),
]


# ── Handlers ──────────────────────────────────────────────────────────

def _cfg(config):
    """Extract base_url, token, login from config."""
    return {
        "base_url": config.get("base_url", "https://api.direct.yandex.com/json/v5"),
        "token": config["token"],
        "login": config.get("login", ""),
    }


# --- VCards ---

async def _handle_vcards_add(client, args, config):
    c = _cfg(config)
    vcard = {
        "CampaignId": args["campaign_id"],
        "CompanyName": args["company"],
        "Phone": {"CountryCode": "+7", "CityCode": args["city_code"], "PhoneNumber": args["phone_number"]},
        "Country": args["country"],
        "City": args["city"],
    }
    for src, dst in [("street", "Street"), ("house", "House"), ("work_time", "WorkTime"), ("extra_message", "ExtraMessage")]:
        if v := args.get(src):
            vcard[dst] = v
    data = await _api(client, "vcards", "add", {"VCards": [vcard]}, **c)
    return _result(data.get("result", data))


async def _handle_vcards_get(client, args, config):
    c = _cfg(config)
    criteria = {}
    if ids := args.get("vcard_ids"):
        criteria["Ids"] = ids
    fields = ["Id", "CampaignId", "Country", "City", "Street", "House", "CompanyName", "Phone", "WorkTime", "ExtraMessage"]
    data = await _api(client, "vcards", "get", {"SelectionCriteria": criteria, "FieldNames": fields}, **c)
    return _result(data.get("result", data))


async def _handle_vcards_delete(client, args, config):
    c = _cfg(config)
    data = await _api(client, "vcards", "delete", {"SelectionCriteria": {"Ids": args["vcard_ids"]}}, **c)
    return _result(data.get("result", data))


# --- Feeds ---

async def _handle_feeds_add(client, args, config):
    c = _cfg(config)
    url_feed = {"Url": args["url"]}
    if login := args.get("login"):
        url_feed["Login"] = login
    if password := args.get("password"):
        url_feed["Password"] = password
    if rut := args.get("remove_utm_tags"):
        url_feed["RemoveUtmTags"] = rut
    feed = {"Name": args["name"], "BusinessType": args["business_type"], "SourceType": "URL", "UrlFeed": url_feed}
    data = await _api(client, "feeds", "add", {"Feeds": [feed]}, **c)
    return _result(data.get("result", data))


async def _handle_feeds_get(client, args, config):
    c = _cfg(config)
    criteria = {}
    if ids := args.get("feed_ids"):
        criteria["Ids"] = ids
    fields = ["Id", "Name", "BusinessType", "SourceType", "UrlFeed", "Status", "NumberOfItems"]
    data = await _api(client, "feeds", "get", {"SelectionCriteria": criteria, "FieldNames": fields}, **c)
    return _result(data.get("result", data))


async def _handle_feeds_update(client, args, config):
    c = _cfg(config)
    feed = {"Id": args["feed_id"]}
    if name := args.get("name"):
        feed["Name"] = name
    url_feed = {}
    if url := args.get("url"):
        url_feed["Url"] = url
    if login := args.get("login"):
        url_feed["Login"] = login
    if password := args.get("password"):
        url_feed["Password"] = password
    if url_feed:
        feed["UrlFeed"] = url_feed
    data = await _api(client, "feeds", "update", {"Feeds": [feed]}, **c)
    return _result(data.get("result", data))


async def _handle_feeds_delete(client, args, config):
    c = _cfg(config)
    data = await _api(client, "feeds", "delete", {"SelectionCriteria": {"Ids": args["feed_ids"]}}, **c)
    return _result(data.get("result", data))


# --- Smart Ad Targets ---

async def _handle_smart_targets_add(client, args, config):
    c = _cfg(config)
    target = {"AdGroupId": args["adgroup_id"], "Name": args["name"]}
    if aio := args.get("available_items_only"):
        target["AvailableItemsOnly"] = aio
    if conds := args.get("conditions"):
        target["Conditions"] = conds
    data = await _api(client, "smartadtargets", "add", {"SmartAdTargets": [target]}, **c)
    return _result(data.get("result", data))


async def _handle_smart_targets_get(client, args, config):
    c = _cfg(config)
    criteria = {}
    if ids := args.get("campaign_ids"):
        criteria["CampaignIds"] = ids
    if ids := args.get("adgroup_ids"):
        criteria["AdGroupIds"] = ids
    if ids := args.get("target_ids"):
        criteria["Ids"] = ids
    fields = ["Id", "AdGroupId", "CampaignId", "Name", "AvailableItemsOnly", "Conditions", "State", "StatusClarification"]
    data = await _api(client, "smartadtargets", "get", {"SelectionCriteria": criteria, "FieldNames": fields}, **c)
    return _result(data.get("result", data))


async def _handle_smart_targets_action(client, args, config):
    c = _cfg(config)
    action = args["action"]
    data = await _api(client, "smartadtargets", action, {"SelectionCriteria": {"Ids": args["target_ids"]}}, **c)
    return _result(data.get("result", data))


# --- Ad type tools ---

async def _handle_ads_add_dynamic(client, args, config):
    c = _cfg(config)
    dyn = {"Text": args["text"]}
    if h := args.get("ad_image_hash"):
        dyn["AdImageHash"] = h
    if s := args.get("sitelink_set_id"):
        dyn["SitelinkSetId"] = s
    ad = {"AdGroupId": args["adgroup_id"], "DynamicTextAd": dyn}
    data = await _api(client, "ads", "add", {"Ads": [ad]}, **c)
    return _result(data.get("result", data))


async def _handle_ads_add_image(client, args, config):
    c = _cfg(config)
    ad = {
        "AdGroupId": args["adgroup_id"],
        "TextImageAd": {"AdImageHash": args["ad_image_hash"], "Href": args["href"]},
    }
    data = await _api(client, "ads", "add", {"Ads": [ad]}, **c)
    return _result(data.get("result", data))


async def _handle_ads_add_shopping(client, args, config):
    c = _cfg(config)
    shopping = {"FeedId": args["feed_id"]}
    if conds := args.get("conditions"):
        shopping["Conditions"] = conds
    if dt := args.get("default_texts"):
        shopping["DefaultTexts"] = dt
    if s := args.get("sitelink_set_id"):
        shopping["SitelinkSetId"] = s
    ad = {"AdGroupId": args["adgroup_id"], "ShoppingAd": shopping}
    data = await _api501(client, "ads", "add", {"Ads": [ad]}, **c)
    return _result(data.get("result", data))


# --- Video / Creative tools ---

async def _handle_videos_upload(client, args, config):
    c = _cfg(config)
    file_path = args["file_path"]
    with open(file_path, "rb") as f:
        video_data = base64.b64encode(f.read()).decode("ascii")
    name = args.get("name") or file_path.rsplit("/", 1)[-1]
    video = {"VideoData": video_data, "Name": name}
    data = await _api(client, "advideos", "add", {"AdVideos": [video]}, timeout=300, **c)
    return _result(data.get("result", data))


async def _handle_videos_get(client, args, config):
    c = _cfg(config)
    criteria = {}
    if ids := args.get("video_ids"):
        criteria["Ids"] = ids
    fields = ["Id", "Name", "Status", "Duration", "PreviewUrl"]
    data = await _api(client, "advideos", "get", {"SelectionCriteria": criteria, "FieldNames": fields}, **c)
    return _result(data.get("result", data))


async def _handle_creatives_add(client, args, config):
    c = _cfg(config)
    creative = {"VideoExtensionCreative": {"VideoId": args["video_id"]}}
    data = await _api(client, "creatives", "add", {"Creatives": [creative]}, **c)
    return _result(data.get("result", data))


async def _handle_creatives_get(client, args, config):
    c = _cfg(config)
    criteria = {}
    if ids := args.get("creative_ids"):
        criteria["Ids"] = ids
    if types := args.get("types"):
        criteria["Types"] = types
    fields = ["Id", "Type", "Name", "PreviewUrl", "VideoExtensionCreative"]
    data = await _api(client, "creatives", "get", {"SelectionCriteria": criteria, "FieldNames": fields}, **c)
    return _result(data.get("result", data))


# --- Misc tools ---

async def _handle_callouts_link(client, args, config):
    c = _cfg(config)
    extensions = [{"AdExtensionId": cid, "Operation": "SET"} for cid in args["callout_ids"]]
    ad = {"Id": args["ad_id"], "TextAd": {"CalloutSetting": {"AdExtensions": extensions}}}
    data = await _api501(client, "ads", "update", {"Ads": [ad]}, **c)
    return _result(data.get("result", data))


async def _handle_bid_modifiers_toggle(client, args, config):
    c = _cfg(config)
    enabled = "YES" if args["enabled"] else "NO"
    items = [{"BidModifierId": mid, "Enabled": enabled} for mid in args["bid_modifier_ids"]]
    data = await _api(client, "bidmodifiers", "toggle", {"BidModifierToggleItems": items}, **c)
    return _result(data.get("result", data))


async def _handle_adgroups_update(client, args, config):
    c = _cfg(config)
    group = {"Id": args["adgroup_id"]}
    if name := args.get("name"):
        group["Name"] = name
    if rids := args.get("region_ids"):
        group["RegionIds"] = rids
    if nk := args.get("negative_keywords"):
        group["NegativeKeywords"] = {"Items": nk}
    if tp := args.get("tracking_params"):
        group["TrackingParams"] = tp
    data = await _api(client, "adgroups", "update", {"AdGroups": [group]}, **c)
    return _result(data.get("result", data))


async def _handle_regions_get(client, args, config):
    c = _cfg(config)
    data = await _api(client, "dictionaries", "get", {"DictionaryNames": ["GeoRegions"]}, **c)
    return _result(data.get("result", data))


async def _handle_interests_get(client, args, config):
    c = _cfg(config)
    data = await _api(client, "dictionaries", "get", {"DictionaryNames": ["Interests"]}, **c)
    return _result(data.get("result", data))


async def _handle_wordstat_user_info(client, args, config):
    oauth_token = config["token"]
    folder_id = config.get("folder_id", "")
    iam = await _get_iam(client, oauth_token)
    url = "https://searchapi.api.cloud.yandex.net/v2/wordstat/userInfo"
    headers = {"Authorization": f"Bearer {iam}", "Content-Type": "application/json"}
    body = {"folderId": folder_id}
    resp = await client.post(url, headers=headers, json=body, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Wordstat userInfo error {resp.status_code}: {resp.text[:500]}")
    return _result(resp.json())


# ── Registration ──────────────────────────────────────────────────────

_HANDLER_MAP = {
    "yd_vcards_add": _handle_vcards_add,
    "yd_vcards_get": _handle_vcards_get,
    "yd_vcards_delete": _handle_vcards_delete,
    "yd_feeds_add": _handle_feeds_add,
    "yd_feeds_get": _handle_feeds_get,
    "yd_feeds_update": _handle_feeds_update,
    "yd_feeds_delete": _handle_feeds_delete,
    "yd_smart_targets_add": _handle_smart_targets_add,
    "yd_smart_targets_get": _handle_smart_targets_get,
    "yd_smart_targets_action": _handle_smart_targets_action,
    "yd_ads_add_dynamic": _handle_ads_add_dynamic,
    "yd_ads_add_image": _handle_ads_add_image,
    "yd_ads_add_shopping": _handle_ads_add_shopping,
    "yd_videos_upload": _handle_videos_upload,
    "yd_videos_get": _handle_videos_get,
    "yd_creatives_add": _handle_creatives_add,
    "yd_creatives_get": _handle_creatives_get,
    "yd_callouts_link": _handle_callouts_link,
    "yd_bid_modifiers_toggle": _handle_bid_modifiers_toggle,
    "yd_adgroups_update": _handle_adgroups_update,
    "yd_regions_get": _handle_regions_get,
    "yd_interests_get": _handle_interests_get,
    "yd_wordstat_user_info": _handle_wordstat_user_info,
}


# ── ExcludedSites ──────────────────────────────────────────────────────

async def _handle_excluded_sites_get(client, args, config):
    data = await _api(client, "campaigns", "get", {
        "SelectionCriteria": {"Ids": [args["campaign_id"]]},
        "FieldNames": ["Id", "Name", "ExcludedSites"],
    }, **config)
    camps = data.get("result", {}).get("Campaigns", [])
    if camps:
        sites = (camps[0].get("ExcludedSites") or {}).get("Items") or []
        return _result({"campaign_id": args["campaign_id"], "excluded_sites": sites, "count": len(sites)})
    return _result({"error": "Campaign not found"})


async def _handle_excluded_sites_update(client, args, config):
    data = await _api(client, "campaigns", "update", {
        "Campaigns": [{
            "Id": args["campaign_id"],
            "ExcludedSites": {"Items": args["sites"]},
        }]
    }, **config)
    return _result(data.get("result", data))


async def _handle_blocked_ips_update(client, args, config):
    data = await _api(client, "campaigns", "update", {
        "Campaigns": [{
            "Id": args["campaign_id"],
            "BlockedIps": {"Items": args["ips"]},
        }]
    }, **config)
    return _result(data.get("result", data))


# ── Campaign Strategy Update ──────────────────────────────────────────

async def _handle_campaign_strategy_update(client, args, config):
    """Update campaign bidding strategy (CPA, weekly limit, goal, strategy type)."""
    campaign = {"Id": args["campaign_id"], "TextCampaign": {"BiddingStrategy": {}}}
    strategy = campaign["TextCampaign"]["BiddingStrategy"]

    search_type = args.get("strategy_search")
    network_type = args.get("strategy_network")

    if search_type:
        search_obj = {"BiddingStrategyType": search_type}
        if search_type == "PAY_FOR_CONVERSION":
            params = {}
            if args.get("goal_id"):
                params["GoalId"] = args["goal_id"]
            if args.get("cpa"):
                params["Cpa"] = int(args["cpa"] * 1_000_000)
            if args.get("weekly_spend_limit"):
                params["WeeklySpendLimit"] = int(args["weekly_spend_limit"] * 1_000_000)
            search_obj["PayForConversion"] = params
        elif search_type == "WB_MAXIMUM_CLICKS":
            params = {}
            if args.get("weekly_spend_limit"):
                params["WeeklySpendLimit"] = int(args["weekly_spend_limit"] * 1_000_000)
            search_obj["WbMaximumClicks"] = params
        elif search_type == "WB_MAXIMUM_CONVERSION_RATE":
            params = {}
            if args.get("goal_id"):
                params["GoalId"] = args["goal_id"]
            if args.get("weekly_spend_limit"):
                params["WeeklySpendLimit"] = int(args["weekly_spend_limit"] * 1_000_000)
            search_obj["WbMaximumConversionRate"] = params
        elif search_type == "SERVING_OFF":
            pass
        strategy["Search"] = search_obj

    if network_type:
        network_obj = {"BiddingStrategyType": network_type}
        if network_type == "WB_MAXIMUM_CONVERSION_RATE":
            params = {}
            if args.get("goal_id"):
                params["GoalId"] = args["goal_id"]
            if args.get("weekly_spend_limit"):
                params["WeeklySpendLimit"] = int(args["weekly_spend_limit"] * 1_000_000)
            network_obj["WbMaximumConversionRate"] = params
        elif network_type == "WB_MAXIMUM_CLICKS":
            params = {}
            if args.get("weekly_spend_limit"):
                params["WeeklySpendLimit"] = int(args["weekly_spend_limit"] * 1_000_000)
            network_obj["WbMaximumClicks"] = params
        elif network_type == "NETWORK_DEFAULT":
            network_obj["NetworkDefault"] = {"LimitPercent": 100}
        elif network_type == "SERVING_OFF":
            pass
        strategy["Network"] = network_obj

    data = await _api(client, "campaigns", "update", {"Campaigns": [campaign]}, **config)
    return _result(data.get("result", data))


# ── Keywords: suspend / resume / delete ───────────────────────────────

async def _handle_keywords_suspend(client, args, config):
    data = await _api(client, "keywords", "suspend", {"SelectionCriteria": {"Ids": args["ids"]}}, **config)
    return _result(data.get("result", data))


async def _handle_keywords_resume(client, args, config):
    data = await _api(client, "keywords", "resume", {"SelectionCriteria": {"Ids": args["ids"]}}, **config)
    return _result(data.get("result", data))


async def _handle_keywords_delete(client, args, config):
    data = await _api(client, "keywords", "delete", {"SelectionCriteria": {"Ids": args["ids"]}}, **config)
    return _result(data.get("result", data))


# ── AudienceTargets: suspend / resume ─────────────────────────────────

async def _handle_audience_targets_suspend(client, args, config):
    data = await _api(client, "audiencetargets", "suspend", {"SelectionCriteria": {"Ids": args["ids"]}}, **config)
    return _result(data.get("result", data))


async def _handle_audience_targets_resume(client, args, config):
    data = await _api(client, "audiencetargets", "resume", {"SelectionCriteria": {"Ids": args["ids"]}}, **config)
    return _result(data.get("result", data))


# ── RetargetingLists: update ──────────────────────────────────────────

async def _handle_retargeting_lists_update(client, args, config):
    item = {"Id": args["id"]}
    if name := args.get("name"):
        item["Name"] = name
    if desc := args.get("description"):
        item["Description"] = desc
    if rules := args.get("rules"):
        item["Rules"] = [{
            "Operator": r["operator"],
            "Arguments": [
                {"ExternalId": g["goal_id"], "MembershipLifeSpan": g["membership_life_span"]}
                for g in r["goals"]
            ],
        } for r in rules]
    data = await _api(client, "retargetinglists", "update", {"RetargetingLists": [item]}, **config)
    return _result(data.get("result", data))


# ── Changes: recent timestamp ─────────────────────────────────────────

async def _handle_changes_timestamp_get(client, args, config):
    """Get current server timestamp via changes.checkDictionaries.

    Unlike changes.check, this endpoint does not require CampaignIds/AdGroupIds/AdIds,
    so it's ideal for obtaining a baseline server timestamp. A sentinel past timestamp
    is used purely to satisfy API validation — the response Timestamp is what we need.
    """
    data = await _api(client, "changes", "checkDictionaries", {"Timestamp": "2020-01-01T00:00:00Z"}, **config)
    result = data.get("result", {})
    return _result({"timestamp": result.get("Timestamp", ""), "dictionaries": result.get("Dictionaries", [])})


# ── Tool definitions for new tools ────────────────────────────────────

_NEW_TOOLS = [
    Tool(
        name="yd_excluded_sites_get",
        description="Get list of excluded sites (blocked placements) for a campaign.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
            },
            "required": ["campaign_id"],
        },
    ),
    Tool(
        name="yd_excluded_sites_update",
        description="Set excluded sites (blocked placements) for a campaign. Replaces entire list.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
                "sites": {"type": "array", "items": {"type": "string"}, "description": "List of sites to exclude (e.g. ['games.yandex.ru', 'AdsNative', 'example.com'])"},
            },
            "required": ["campaign_id", "sites"],
        },
    ),
    Tool(
        name="yd_blocked_ips_update",
        description="Set blocked IPs for a campaign (max 25). Only exact IPs, no subnets.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
                "ips": {"type": "array", "items": {"type": "string"}, "description": "List of IPs to block (e.g. ['1.2.3.4', '5.6.7.8'])"},
            },
            "required": ["campaign_id", "ips"],
        },
    ),
    Tool(
        name="yd_campaign_strategy_update",
        description="Update campaign bidding strategy. Change CPA, weekly limit, goal, or strategy type.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
                "strategy_search": {"type": "string", "enum": ["PAY_FOR_CONVERSION", "WB_MAXIMUM_CLICKS", "WB_MAXIMUM_CONVERSION_RATE", "SERVING_OFF"], "description": "Search strategy type"},
                "strategy_network": {"type": "string", "enum": ["SERVING_OFF", "NETWORK_DEFAULT", "WB_MAXIMUM_CLICKS", "WB_MAXIMUM_CONVERSION_RATE"], "description": "Network strategy type"},
                "cpa": {"type": "number", "description": "Target CPA in rubles (for PAY_FOR_CONVERSION)"},
                "goal_id": {"type": "integer", "description": "Metrika goal ID"},
                "weekly_spend_limit": {"type": "number", "description": "Weekly spend limit in rubles"},
            },
            "required": ["campaign_id"],
        },
    ),
    Tool(
        name="yd_keywords_suspend",
        description="Suspend (pause) keywords by IDs. Stops impressions without deleting.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Keyword IDs to suspend"},
            },
            "required": ["ids"],
        },
    ),
    Tool(
        name="yd_keywords_resume",
        description="Resume previously suspended keywords by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Keyword IDs to resume"},
            },
            "required": ["ids"],
        },
    ),
    Tool(
        name="yd_keywords_delete",
        description="Permanently delete keywords by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Keyword IDs to delete"},
            },
            "required": ["ids"],
        },
    ),
    Tool(
        name="yd_audience_targets_suspend",
        description="Suspend (pause) audience targets by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Audience target IDs to suspend"},
            },
            "required": ["ids"],
        },
    ),
    Tool(
        name="yd_audience_targets_resume",
        description="Resume previously suspended audience targets by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Audience target IDs to resume"},
            },
            "required": ["ids"],
        },
    ),
    Tool(
        name="yd_retargeting_lists_update",
        description="Update an existing retargeting list. Any field (name, description, rules) may be changed.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Retargeting list ID to update"},
                "name": {"type": "string", "description": "New name (optional)"},
                "description": {"type": "string", "description": "New description (optional)"},
                "rules": {
                    "type": "array",
                    "description": "Replacement rules (optional). Each rule: {operator: AND|OR|NOT, goals: [{goal_id, membership_life_span}]}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operator": {"type": "string", "enum": ["AND", "OR", "NOT"]},
                            "goals": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "goal_id": {"type": "integer"},
                                        "membership_life_span": {"type": "integer", "description": "Days (1-540)"},
                                    },
                                    "required": ["goal_id", "membership_life_span"],
                                },
                            },
                        },
                        "required": ["operator", "goals"],
                    },
                },
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="yd_changes_timestamp_get",
        description="Get current server timestamp (UTC) from Yandex Direct. Use as starting point for yd_changes_check.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


def register_extra_direct_handlers(dispatch: dict):
    """Add all extra tool handlers to the dispatch dict.

    Each handler signature: async def handler(client, args, config) -> list[TextContent]
    """
    dispatch.update(_HANDLER_MAP)
    # Add new operational tools
    dispatch["yd_excluded_sites_get"] = _handle_excluded_sites_get
    dispatch["yd_excluded_sites_update"] = _handle_excluded_sites_update
    dispatch["yd_blocked_ips_update"] = _handle_blocked_ips_update
    dispatch["yd_campaign_strategy_update"] = _handle_campaign_strategy_update
    dispatch["yd_keywords_suspend"] = _handle_keywords_suspend
    dispatch["yd_keywords_resume"] = _handle_keywords_resume
    dispatch["yd_keywords_delete"] = _handle_keywords_delete
    dispatch["yd_audience_targets_suspend"] = _handle_audience_targets_suspend
    dispatch["yd_audience_targets_resume"] = _handle_audience_targets_resume
    dispatch["yd_retargeting_lists_update"] = _handle_retargeting_lists_update
    dispatch["yd_changes_timestamp_get"] = _handle_changes_timestamp_get


# Append new tools to the main list
EXTRA_DIRECT_TOOLS.extend(_NEW_TOOLS)
