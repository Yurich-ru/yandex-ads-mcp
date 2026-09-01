#!/usr/bin/env python3
"""MCP Server for Yandex Direct API v5, Yandex Metrika API, Yandex Audience API, and Wordstat API.

Provides 151 tools for managing advertising campaigns, audiences, web analytics, and keyword research.
See README.md for setup instructions.
"""

import os
import sys
import json
import asyncio
import logging

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ── Config ─────────────────────────────────────────────────────────────
def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "")
    if v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


API_URL = os.environ.get("YD_API_URL", "https://api.direct.yandex.com/json/v5")
SANDBOX_URL = "https://api-sandbox.direct.yandex.com/json/v5"
WORDSTAT_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
TOKEN = os.environ.get("YD_OAUTH_TOKEN", "")
# Optional per-service tokens for multi-account setups (e.g. Direct lives in one
# Yandex account, Metrika/Audience in another). Fall back to YD_OAUTH_TOKEN.
METRIKA_TOKEN = os.environ.get("YD_METRIKA_TOKEN", "") or TOKEN
AUDIENCE_TOKEN = os.environ.get("YD_AUDIENCE_TOKEN", "") or TOKEN
YC_FOLDER_ID = os.environ.get("YC_FOLDER_ID", "")
# Service-account API key for Wordstat (Search API). Required for OAuth tokens
# issued after 2026-06-01: Yandex Cloud no longer exchanges them for IAM tokens.
YC_API_KEY = os.environ.get("YC_API_KEY", "")
USE_SANDBOX = _env_bool("YD_SANDBOX")
LOGIN = os.environ.get("YD_LOGIN", "")  # optional default Client-Login for agency accounts


def _parse_token_map(raw: str) -> dict:
    """'login:token,login2:token2' -> {login: token}; malformed entries are skipped."""
    out = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        login, token = pair.split(":", 1)
        if login.strip() and token.strip():
            out[login.strip()] = token.strip()
    return out


# Extra Direct cabinets (non-agency multi-account): each login gets its own token.
# Selected per call via the client_login argument (or YD_LOGIN as default).
DIRECT_TOKENS = _parse_token_map(os.environ.get("YD_DIRECT_TOKENS", ""))

# Safety / access control
READONLY = _env_bool("YD_READONLY")   # block every mutating tool (add/update/delete/action/set/...)
CONFIRM = _env_bool("YD_CONFIRM")     # require confirm=true on every mutating tool
ALLOWED_LOGINS = [s.strip() for s in os.environ.get("YD_ALLOWED_LOGINS", "").split(",") if s.strip()]

# Logging
LOG_LEVEL = os.environ.get("YD_LOG_LEVEL", "INFO").upper()
LOG_FILE = os.environ.get("YD_LOG_FILE", "")   # empty → stderr only (no file written)
LOG_BODIES = _env_bool("YD_LOG_BODIES")        # dump request/response bodies (verbose; may contain ad data)

# ── Logging setup ──────────────────────────────────────────────────────
logger = logging.getLogger("yandex-ads-mcp")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_sh = logging.StreamHandler(sys.stderr)
_sh.setFormatter(_fmt)
logger.addHandler(_sh)
if LOG_FILE:
    _fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _fh.setFormatter(_fmt)
    logger.addHandler(_fh)


def _log_body(prefix, *args):
    """Log request/response bodies only when YD_LOG_BODIES is enabled (verbose)."""
    if LOG_BODIES:
        logger.debug(prefix, *args)


# ── Extra modules ──────────────────────────────────────────────────────
from tools_metrika import METRIKA_TOOLS, register_metrika_handlers
from tools_audience import AUDIENCE_TOOLS, register_audience_handlers
from tools_direct_extra import (
    EXTRA_DIRECT_TOOLS,
    register_extra_direct_handlers,
    client_login_var,
    annotate_partial,
    set_log_bodies,
    set_direct_tokens,
    direct_headers,
    request_with_retry,
    iam_expiry,
    log_units,
)

set_log_bodies(LOG_BODIES)
set_direct_tokens(DIRECT_TOKENS)

server = Server("yandex-ads")


def _base_url():
    return SANDBOX_URL if USE_SANDBOX else API_URL


def _effective_login() -> str:
    """Per-call Client-Login (multi-account) overrides the global default."""
    return client_login_var.get("") or LOGIN


def _headers():
    """Bearer + optional Client-Login; a login from YD_DIRECT_TOKENS selects its own token."""
    return direct_headers(TOKEN, LOGIN)


async def _api(client: httpx.AsyncClient, service: str, method: str, params: dict) -> dict:
    """Call Yandex Direct API v5."""
    url = f"{_base_url()}/{service}"
    body = {"method": method, "params": params}
    _log_body("REQUEST %s %s: %s", url, method, json.dumps(body, ensure_ascii=False)[:2000])
    resp = await request_with_retry(client, url, headers=_headers(), json_body=body, timeout=120)
    log_units(resp)
    data = resp.json()
    _log_body("RESPONSE %s: %s", resp.status_code, json.dumps(data, ensure_ascii=False)[:2000])
    if "error" in data:
        raise Exception(f"API error {data['error'].get('error_code')}: {data['error'].get('error_detail', data['error'].get('error_string'))}")
    return annotate_partial(data)


# ── Access control ─────────────────────────────────────────────────────

_MUTATING_TOKENS = ("_add", "_create", "_update", "_delete", "_action",
                    "_set", "_toggle", "_link", "_unlink", "_upload",
                    "_suspend", "_resume", "_confirm", "_reprocess", "_undelete")


def _is_mutating(name: str) -> bool:
    """True for any tool that changes state (create/update/delete/action/...)."""
    return any(t in name for t in _MUTATING_TOKENS)


_AUDIENCE_NAMES = frozenset(t.name for t in AUDIENCE_TOOLS)


def _is_direct(name: str) -> bool:
    """True for Yandex Direct tools (which use Client-Login); Metrika/Wordstat/Audience don't."""
    return (name.startswith("yd_")
            and not name.startswith("yd_metrika")
            and not name.startswith("yd_wordstat")
            and name not in _AUDIENCE_NAMES)


def _deny(reason: str):
    logger.warning("DENIED: %s", reason)
    return [TextContent(type="text", text=json.dumps({"denied": True, "reason": reason}, ensure_ascii=False))]


def _confirm_preview(name: str, arguments: dict, login: str):
    preview = {
        "confirm_required": True,
        "tool": name,
        "client_login": login or None,
        "arguments": arguments,
        "note": "Mutating operation and YD_CONFIRM is enabled. Re-call this tool with confirm=true to execute.",
    }
    return [TextContent(type="text", text=json.dumps(preview, indent=2, ensure_ascii=False))]


def _result(data):
    return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]


# ── Tools ──────────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="yd_campaigns_get",
        description="Get list of campaigns. Filters: types, states, statuses, ids.",
        inputSchema={
            "type": "object",
            "properties": {
                "types": {"type": "array", "items": {"type": "string"}, "description": "TEXT_CAMPAIGN, UNIFIED_CAMPAIGN, etc."},
                "states": {"type": "array", "items": {"type": "string"}, "description": "ON, OFF, SUSPENDED, ARCHIVED, etc."},
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Campaign IDs"},
            },
        },
    ),
    Tool(
        name="yd_campaigns_add",
        description="Create a new text campaign. Supports all strategies: PAY_FOR_CONVERSION, WB_MAXIMUM_CLICKS, WB_MAXIMUM_CONVERSION_RATE, AVERAGE_CPA, SERVING_OFF, etc.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Campaign name (max 255 chars)"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD (optional)"},
                "daily_budget_amount": {"type": "number", "description": "Daily budget in rubles (e.g. 500.00)"},
                "daily_budget_mode": {"type": "string", "enum": ["STANDARD", "DISTRIBUTED"], "description": "Budget spending mode"},
                "strategy_search": {"type": "string", "enum": ["WB_MAXIMUM_CLICKS", "PAY_FOR_CONVERSION", "PAY_FOR_CONVERSION_MULTIPLE_GOALS", "WB_MAXIMUM_CONVERSION_RATE", "AVERAGE_CPA", "AVERAGE_CPC", "HIGHEST_POSITION", "SERVING_OFF"], "description": "Search strategy type"},
                "strategy_network": {"type": "string", "enum": ["SERVING_OFF", "NETWORK_DEFAULT", "WB_MAXIMUM_CLICKS", "WB_MAXIMUM_CONVERSION_RATE", "AVERAGE_CPC"], "description": "Network strategy type"},
                "weekly_spend_limit": {"type": "number", "description": "Weekly spend limit in rubles (for WB_ strategies and PAY_FOR_CONVERSION)"},
                "goal_id": {"type": "integer", "description": "Metrika goal ID for conversion strategies"},
                "goal_cpa": {"type": "number", "description": "Target CPA in rubles (for PAY_FOR_CONVERSION / AVERAGE_CPA)"},
                "counter_ids": {"type": "array", "items": {"type": "integer"}, "description": "Yandex Metrika counter IDs"},
                "priority_goals": {"type": "array", "items": {"type": "object", "properties": {"goal_id": {"type": "integer"}, "value": {"type": "number", "description": "Goal value in rubles"}}, "required": ["goal_id", "value"]}, "description": "Priority goals with values for optimization"},
                "negative_keywords": {"type": "array", "items": {"type": "string"}, "description": "Campaign-level negative keywords"},
                "region_ids": {"type": "array", "items": {"type": "integer"}, "description": "Region IDs for targeting"},
            },
            "required": ["name", "start_date"],
        },
    ),
    Tool(
        name="yd_campaigns_update",
        description="Update campaign settings (name, budget, strategy, status, etc.).",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
                "name": {"type": "string"},
                "daily_budget_amount": {"type": "number", "description": "Daily budget in rubles"},
                "daily_budget_mode": {"type": "string", "enum": ["STANDARD", "DISTRIBUTED"]},
                "negative_keywords": {"type": "array", "items": {"type": "string"}},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["campaign_id"],
        },
    ),
    Tool(
        name="yd_campaigns_action",
        description="Suspend, resume, archive, or unarchive campaigns.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_ids": {"type": "array", "items": {"type": "integer"}, "description": "Campaign IDs"},
                "action": {"type": "string", "enum": ["suspend", "resume", "archive", "unarchive"], "description": "Action to perform"},
            },
            "required": ["campaign_ids", "action"],
        },
    ),
    Tool(
        name="yd_adgroups_add",
        description="Create ad groups in a campaign.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID"},
                "groups": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "region_ids": {"type": "array", "items": {"type": "integer"}, "description": "Region IDs (0 = all)"},
                            "negative_keywords": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "region_ids"],
                    },
                    "description": "Array of ad groups to create",
                },
            },
            "required": ["campaign_id", "groups"],
        },
    ),
    Tool(
        name="yd_adgroups_get",
        description="Get ad groups by campaign or group IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_ids": {"type": "array", "items": {"type": "integer"}},
                "group_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_ads_add",
        description="Create text ads in ad groups. Supports bulk creation, sitelinks, and images.",
        inputSchema={
            "type": "object",
            "properties": {
                "ads": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ad_group_id": {"type": "integer"},
                            "title": {"type": "string", "description": "Ad title (max 56 chars)"},
                            "title2": {"type": "string", "description": "Second title (max 30 chars, optional)"},
                            "text": {"type": "string", "description": "Ad text (max 81 chars)"},
                            "href": {"type": "string", "description": "Landing page URL"},
                            "mobile": {"type": "string", "enum": ["YES", "NO"], "description": "Mobile-only ad"},
                            "sitelink_set_id": {"type": "integer", "description": "Sitelink set ID to attach"},
                            "ad_image_hash": {"type": "string", "description": "Image hash (from yd_ad_images_add)"},
                        },
                        "required": ["ad_group_id", "title", "text", "href"],
                    },
                },
            },
            "required": ["ads"],
        },
    ),
    Tool(
        name="yd_ads_update",
        description="Update existing text ads. Can change title, text, href, sitelinks, image, etc.",
        inputSchema={
            "type": "object",
            "properties": {
                "ads": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "description": "Ad ID to update"},
                            "title": {"type": "string", "description": "New title (max 56 chars)"},
                            "title2": {"type": "string", "description": "New second title (max 30 chars)"},
                            "text": {"type": "string", "description": "New text (max 81 chars)"},
                            "href": {"type": "string", "description": "New landing page URL"},
                            "sitelink_set_id": {"type": "integer", "description": "Sitelink set ID"},
                            "ad_image_hash": {"type": "string", "description": "Image hash"},
                        },
                        "required": ["id"],
                    },
                },
            },
            "required": ["ads"],
        },
    ),
    Tool(
        name="yd_ads_get",
        description="Get ads by campaign, ad group, or ad IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_ids": {"type": "array", "items": {"type": "integer"}},
                "ad_group_ids": {"type": "array", "items": {"type": "integer"}},
                "ad_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_ads_action",
        description="Moderate, suspend, resume, archive, or unarchive ads.",
        inputSchema={
            "type": "object",
            "properties": {
                "ad_ids": {"type": "array", "items": {"type": "integer"}},
                "action": {"type": "string", "enum": ["moderate", "suspend", "resume", "archive", "unarchive"]},
            },
            "required": ["ad_ids", "action"],
        },
    ),
    Tool(
        name="yd_keywords_add",
        description="Add keywords to ad groups.",
        inputSchema={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ad_group_id": {"type": "integer"},
                            "keyword": {"type": "string", "description": "Keyword phrase"},
                            "bid": {"type": "number", "description": "Search bid in rubles (optional)"},
                        },
                        "required": ["ad_group_id", "keyword"],
                    },
                },
            },
            "required": ["keywords"],
        },
    ),
    Tool(
        name="yd_keywords_get",
        description="Get keywords by campaign, ad group, or keyword IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_ids": {"type": "array", "items": {"type": "integer"}},
                "ad_group_ids": {"type": "array", "items": {"type": "integer"}},
                "keyword_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_keywords_research",
        description="Deduplicate keywords: merge duplicates, eliminate overlapping phrases. Preprocesses keywords before adding to campaigns.",
        inputSchema={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Array of keyword phrases to deduplicate",
                },
                "operations": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["MERGE_DUPLICATES", "ELIMINATE_OVERLAPPING"]},
                    "description": "Operations to perform (default: both)",
                },
            },
            "required": ["keywords"],
        },
    ),
    Tool(
        name="yd_bids_set",
        description="Set bids for keywords.",
        inputSchema={
            "type": "object",
            "properties": {
                "bids": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "keyword_id": {"type": "integer"},
                            "search_bid": {"type": "number", "description": "Search bid in rubles"},
                            "network_bid": {"type": "number", "description": "Network bid in rubles"},
                        },
                        "required": ["keyword_id"],
                    },
                },
            },
            "required": ["bids"],
        },
    ),
    Tool(
        name="yd_report",
        description="Get campaign statistics report. Returns TSV data.",
        inputSchema={
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "report_type": {
                    "type": "string",
                    "enum": [
                        "ACCOUNT_PERFORMANCE_REPORT",
                        "CAMPAIGN_PERFORMANCE_REPORT",
                        "ADGROUP_PERFORMANCE_REPORT",
                        "AD_PERFORMANCE_REPORT",
                        "CRITERIA_PERFORMANCE_REPORT",
                        "SEARCH_QUERY_PERFORMANCE_REPORT",
                    ],
                    "description": "Report type",
                },
                "field_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fields: CampaignName, AdGroupName, Impressions, Clicks, Cost, Ctr, AvgCpc, etc.",
                },
                "campaign_ids": {"type": "array", "items": {"type": "integer"}, "description": "Filter by campaigns"},
            },
            "required": ["date_from", "date_to", "report_type", "field_names"],
        },
    ),
    Tool(
        name="yd_dictionaries",
        description="Get reference data: regions, currencies, ad categories, etc.",
        inputSchema={
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["Currencies", "MetroStations", "GeoRegions", "TimeZones", "Constants", "AdCategories", "OperationSystemVersions", "SupplySidePlatforms", "Interests", "AudienceCriteriaTypes"],
                    },
                    "description": "Dictionary names to retrieve",
                },
            },
            "required": ["names"],
        },
    ),
    # ── High priority: hasSearchVolume ─────────────────────────────────
    Tool(
        name="yd_keywords_has_volume",
        description="Check if keywords have search volume (impressions) in specified regions. Returns YES/NO per device type. Max 10000 keywords, max 20 requests per 60 seconds.",
        inputSchema={
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}, "description": "Keywords to check (max 10000)"},
                "region_ids": {"type": "array", "items": {"type": "integer"}, "description": "Region IDs (0 = all regions)"},
            },
            "required": ["keywords", "region_ids"],
        },
    ),
    # ── High priority: KeywordBids ─────────────────────────────────────
    Tool(
        name="yd_keyword_bids_get",
        description="Get keyword bids and traffic forecasts.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_ids": {"type": "array", "items": {"type": "integer"}},
                "ad_group_ids": {"type": "array", "items": {"type": "integer"}},
                "keyword_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_keyword_bids_set",
        description="Set keyword bids (search and network).",
        inputSchema={
            "type": "object",
            "properties": {
                "bids": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "keyword_id": {"type": "integer"},
                            "search_bid": {"type": "number", "description": "Search bid in rubles"},
                            "network_bid": {"type": "number", "description": "Network bid in rubles"},
                        },
                        "required": ["keyword_id"],
                    },
                },
            },
            "required": ["bids"],
        },
    ),
    Tool(
        name="yd_keyword_bids_set_auto",
        description="Set automatic bidding for keywords based on target position or other criteria.",
        inputSchema={
            "type": "object",
            "properties": {
                "bids": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "keyword_id": {"type": "integer"},
                            "scope": {"type": "string", "enum": ["SEARCH", "NETWORK", "SEARCH_AND_NETWORK"]},
                            "position": {"type": "string", "enum": ["PREMIUMBLOCK", "FOOTERBLOCK", "P11", "P12", "P13", "P14", "P21", "P22", "P23", "P24"], "description": "Target position"},
                            "max_bid": {"type": "number", "description": "Max bid in rubles"},
                            "increase_percent": {"type": "integer", "description": "Max increase percent (0-1200)"},
                        },
                        "required": ["keyword_id"],
                    },
                },
            },
            "required": ["bids"],
        },
    ),
    # ── High priority: BidModifiers ────────────────────────────────────
    Tool(
        name="yd_bid_modifiers_add",
        description="Add bid modifiers (adjustments) for demographics, devices, regions, etc.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID (or use ad_group_id)"},
                "ad_group_id": {"type": "integer", "description": "Ad group ID (or use campaign_id)"},
                "mobile_adjustment": {"type": "integer", "description": "Mobile bid modifier percent (0-1300, 0=disable)"},
                "desktop_adjustment": {"type": "integer", "description": "Desktop bid modifier percent (0-1300)"},
                "tablet_adjustment": {"type": "integer", "description": "Tablet bid modifier percent (0-1300)"},
                "demographics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "gender": {"type": "string", "enum": ["GENDER_MALE", "GENDER_FEMALE"]},
                            "age": {"type": "string", "enum": ["AGE_0_17", "AGE_18_24", "AGE_25_34", "AGE_35_44", "AGE_45_54", "AGE_55"]},
                            "bid_modifier": {"type": "integer", "description": "Modifier percent (0-1300)"},
                        },
                        "required": ["bid_modifier"],
                    },
                    "description": "Demographic adjustments (gender/age)",
                },
                "regional": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "region_id": {"type": "integer"},
                            "bid_modifier": {"type": "integer", "description": "Modifier percent (10-1300)"},
                        },
                        "required": ["region_id", "bid_modifier"],
                    },
                    "description": "Regional adjustments",
                },
            },
        },
    ),
    Tool(
        name="yd_bid_modifiers_get",
        description="Get bid modifiers for campaigns or ad groups.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_ids": {"type": "array", "items": {"type": "integer"}},
                "ad_group_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_bid_modifiers_set",
        description="Update existing bid modifiers by their IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "modifiers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "description": "BidModifier ID"},
                            "bid_modifier": {"type": "integer", "description": "New modifier percent"},
                        },
                        "required": ["id", "bid_modifier"],
                    },
                },
            },
            "required": ["modifiers"],
        },
    ),
    Tool(
        name="yd_bid_modifiers_delete",
        description="Delete bid modifiers by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "BidModifier IDs to delete"},
            },
            "required": ["ids"],
        },
    ),
    # ── High priority: NegativeKeywordSharedSets ───────────────────────
    Tool(
        name="yd_negative_keywords_sets_add",
        description="Create shared negative keyword sets (max 30 total, reusable across campaigns).",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Set name (max 255 chars)"},
                "negative_keywords": {"type": "array", "items": {"type": "string"}, "description": "Negative keyword phrases"},
            },
            "required": ["name", "negative_keywords"],
        },
    ),
    Tool(
        name="yd_negative_keywords_sets_get",
        description="Get shared negative keyword sets.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Set IDs (empty = all)"},
            },
        },
    ),
    Tool(
        name="yd_negative_keywords_sets_update",
        description="Update a shared negative keyword set.",
        inputSchema={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Set ID"},
                "name": {"type": "string"},
                "negative_keywords": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id"],
        },
    ),
    Tool(
        name="yd_negative_keywords_sets_delete",
        description="Delete shared negative keyword sets.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["ids"],
        },
    ),
    # ── High priority: Sitelinks ───────────────────────────────────────
    Tool(
        name="yd_sitelinks_add",
        description="Create sitelink sets (quick links under ads, 1-8 per set).",
        inputSchema={
            "type": "object",
            "properties": {
                "sitelinks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Link text (max 30 chars)"},
                            "href": {"type": "string", "description": "URL (max 1024 chars)"},
                            "description": {"type": "string", "description": "Description (max 60 chars, optional)"},
                        },
                        "required": ["title", "href"],
                    },
                    "description": "Array of 1-8 sitelinks",
                },
            },
            "required": ["sitelinks"],
        },
    ),
    Tool(
        name="yd_sitelinks_get",
        description="Get sitelink sets by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "SitelinkSet IDs"},
            },
            "required": ["ids"],
        },
    ),
    Tool(
        name="yd_sitelinks_delete",
        description="Delete sitelink sets.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["ids"],
        },
    ),
    # ── High priority: AdExtensions (Callouts) ─────────────────────────
    Tool(
        name="yd_ad_extensions_add",
        description="Create ad extensions (callouts — short texts shown under ads, max 25 chars each).",
        inputSchema={
            "type": "object",
            "properties": {
                "callouts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Callout texts (max 25 chars each)",
                },
            },
            "required": ["callouts"],
        },
    ),
    Tool(
        name="yd_ad_extensions_get",
        description="Get ad extensions by IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["ids"],
        },
    ),
    Tool(
        name="yd_ad_extensions_delete",
        description="Delete ad extensions.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["ids"],
        },
    ),
    # ── High priority: Changes ─────────────────────────────────────────
    Tool(
        name="yd_changes_check",
        description="Check what changed since a given timestamp (campaigns, ad groups, ads, stats).",
        inputSchema={
            "type": "object",
            "properties": {
                "timestamp": {"type": "string", "description": "ISO 8601 timestamp, e.g. 2026-04-14T00:00:00Z"},
                "campaign_ids": {"type": "array", "items": {"type": "integer"}, "description": "Campaign IDs to check (max 3000)"},
                "field_names": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["CampaignIds", "AdGroupIds", "AdIds", "CampaignsStat"]},
                    "description": "What changes to detect",
                },
            },
            "required": ["timestamp", "field_names"],
        },
    ),
    # ── Medium priority: AudienceTargets ───────────────────────────────
    Tool(
        name="yd_audience_targets_add",
        description="Add audience targeting conditions to ad groups (retargeting lists or interests).",
        inputSchema={
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ad_group_id": {"type": "integer"},
                            "retargeting_list_id": {"type": "integer", "description": "Retargeting list ID"},
                            "interest_id": {"type": "integer", "description": "Interest category ID (for mobile apps)"},
                            "context_bid": {"type": "number", "description": "Network bid in rubles"},
                            "strategy_priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"]},
                        },
                        "required": ["ad_group_id"],
                    },
                },
            },
            "required": ["targets"],
        },
    ),
    Tool(
        name="yd_audience_targets_get",
        description="Get audience targets by campaign, ad group, or target IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "campaign_ids": {"type": "array", "items": {"type": "integer"}},
                "ad_group_ids": {"type": "array", "items": {"type": "integer"}},
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_audience_targets_delete",
        description="Delete audience targeting conditions.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["ids"],
        },
    ),
    # ── Medium priority: RetargetingLists ──────────────────────────────
    Tool(
        name="yd_retargeting_lists_add",
        description="Create retargeting/audience conditions based on Yandex Metrika goals/segments or "
                    "Yandex Audience segments.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Condition name (max 250 chars)"},
                "description": {"type": "string", "description": "Description (optional)"},
                "type": {"type": "string", "enum": ["RETARGETING", "AUDIENCE"],
                         "description": "AUDIENCE = interests/audience-only condition for ad groups without "
                                        "keywords; default RETARGETING"},
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operator": {"type": "string", "enum": ["ALL", "ANY", "NONE"], "description": "ALL=met all goals, ANY=at least one, NONE=none met"},
                            "goals": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "goal_id": {"type": "integer",
                                                    "description": "Raw ExternalId: Metrika goal = goal id; "
                                                                   "Metrika segment = id + 1000000000 (auto-segment: id + 1500000000); "
                                                                   "Yandex Audience segment = id + 2000000000 (or use audience_segment_id); "
                                                                   "interest = interest id with string prefix 10/20/30 (short/long/any term)"},
                                        "audience_segment_id": {"type": "integer",
                                                                "description": "Yandex Audience segment ID as-is (encoded to ExternalId "
                                                                               "automatically; alternative to goal_id)"},
                                        "membership_life_span": {"type": "integer", "description": "Days (1-540). Required for Metrika goals; ignored for Metrika/Audience segments"},
                                    },
                                },
                            },
                        },
                        "required": ["operator", "goals"],
                    },
                    "description": "Visitor selection rules",
                },
            },
            "required": ["name", "rules"],
        },
    ),
    Tool(
        name="yd_retargeting_lists_get",
        description="Get retargeting lists.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    ),
    Tool(
        name="yd_retargeting_lists_delete",
        description="Delete retargeting lists.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["ids"],
        },
    ),
    # ── Medium priority: AdImages ──────────────────────────────────────
    Tool(
        name="yd_ad_images_add",
        description="Upload ad images (base64-encoded). Max 100 per request (recommended <=3).",
        inputSchema={
            "type": "object",
            "properties": {
                "images": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Image name (max 255 chars)"},
                            "image_data": {"type": "string", "description": "Base64-encoded image data"},
                        },
                        "required": ["name", "image_data"],
                    },
                },
            },
            "required": ["images"],
        },
    ),
    Tool(
        name="yd_ad_images_get",
        description="Get ad images by IDs or linked entities.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}, "description": "Image hashes"},
                "associated": {"type": "boolean", "description": "If true, get images linked to ads"},
            },
        },
    ),
    Tool(
        name="yd_ad_images_delete",
        description="Delete ad images by hashes.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}, "description": "Image hashes to delete"},
            },
            "required": ["ids"],
        },
    ),
    # ── Medium priority: Businesses ────────────────────────────────────
    Tool(
        name="yd_businesses_get",
        description="Get organization profiles from Yandex Business linked to ads.",
        inputSchema={
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Organization IDs (max 10000)"},
            },
            "required": ["ids"],
        },
    ),
    # ── Medium priority: Clients ───────────────────────────────────────
    Tool(
        name="yd_direct_accounts_get",
        description="List Direct cabinets this server can access: the default token plus every "
                    "login from YD_DIRECT_TOKENS, each verified live via Clients.get. Use the "
                    "returned login as client_login on other Direct tools.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="yd_clients_get",
        description="Get advertiser account info (settings, balance, bonuses, etc.).",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    # ── Wordstat API ───────────────────────────────────────────────────
    Tool(
        name="yd_wordstat_top_requests",
        description="Get popular search queries containing a keyword from Yandex Wordstat (last 30 days). Returns phrases with search counts. 1 quota unit per request.",
        inputSchema={
            "type": "object",
            "properties": {
                "phrase": {"type": "string", "description": "Keyword to analyze"},
                "num_phrases": {"type": "integer", "description": "How many related phrases to return, 1-2000 (default 30)"},
                "regions": {"type": "array", "items": {"type": "integer"}, "description": "Region IDs (optional, omit for all Russia)"},
                "devices": {"type": "string", "enum": ["all", "desktop", "phone", "tablet"], "description": "Device filter (default: all)"},
            },
            "required": ["phrase"],
        },
    ),
    Tool(
        name="yd_wordstat_dynamics",
        description="Get search frequency dynamics over time for a keyword. Date alignment is enforced by the API: monthly → both dates must be the 1st of a month; weekly → from=Monday, to=Sunday; daily → any dates.",
        inputSchema={
            "type": "object",
            "properties": {
                "phrase": {"type": "string", "description": "Keyword to analyze"},
                "period": {"type": "string", "enum": ["monthly", "weekly", "daily"], "description": "Time granularity"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD (monthly: 1st of month; weekly: a Monday)"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD (monthly: 1st of month; weekly: a Sunday)"},
                "regions": {"type": "array", "items": {"type": "integer"}, "description": "Region IDs (optional)"},
                "devices": {"type": "string", "enum": ["all", "desktop", "phone", "tablet"]},
            },
            "required": ["phrase", "period", "from_date", "to_date"],
        },
    ),
    Tool(
        name="yd_wordstat_regions",
        description="Get regional distribution of search queries for a keyword. Shows which regions search most. 2 quota units.",
        inputSchema={
            "type": "object",
            "properties": {
                "phrase": {"type": "string", "description": "Keyword to analyze"},
                "region_type": {"type": "string", "enum": ["cities", "regions", "all"], "description": "Granularity level"},
                "devices": {"type": "string", "enum": ["all", "desktop", "phone", "tablet"]},
            },
            "required": ["phrase"],
        },
    ),
    Tool(
        name="yd_wordstat_regions_tree",
        description="Get the full tree of Wordstat-supported regions with IDs. No quota cost. Use to find region IDs for other Wordstat methods.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


def _augment_schema(tool: Tool) -> Tool:
    """Advertise the cross-cutting optional args (client_login, confirm) on tool schemas."""
    schema = json.loads(json.dumps(tool.inputSchema or {"type": "object", "properties": {}}))
    props = schema.setdefault("properties", {})
    if _is_direct(tool.name):
        props.setdefault("client_login", {
            "type": "string",
            "description": "Optional. Select the Direct cabinet for this call: a login listed in "
                           "YD_DIRECT_TOKENS uses its own token; any other login is sent as "
                           "Client-Login (agency sub-client).",
        })
    if CONFIRM and _is_mutating(tool.name):
        props.setdefault("confirm", {
            "type": "boolean",
            "description": "Must be true to execute this mutating call (YD_CONFIRM is enabled).",
        })
    return Tool(name=tool.name, description=tool.description, inputSchema=schema)


@server.list_tools()
async def list_tools():
    return [_augment_schema(t) for t in (TOOLS + METRIKA_TOOLS + EXTRA_DIRECT_TOOLS + AUDIENCE_TOOLS)]


def _rubles_to_micros(rubles: float) -> int:
    """Convert rubles to API micro-units (rubles * 1_000_000)."""
    return int(rubles * 1_000_000)


# ── Handlers ───────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    arguments = dict(arguments or {})
    # Cross-cutting controls (not forwarded to the Yandex API)
    req_login = arguments.pop("client_login", None)
    confirm = bool(arguments.pop("confirm", False))
    mutating = _is_mutating(name)

    # 1) read-only mode
    if mutating and READONLY:
        return _deny(f"Tool '{name}' is blocked: server runs in READ-ONLY mode (YD_READONLY=true).")

    # 2) multi-account whitelist (Direct tools only — Metrika/Wordstat ignore Client-Login)
    effective_login = req_login or LOGIN
    if _is_direct(name) and ALLOWED_LOGINS and effective_login and effective_login not in ALLOWED_LOGINS:
        return _deny(f"Client-Login '{effective_login}' is not in the YD_ALLOWED_LOGINS whitelist.")

    # 3) confirm gate
    if mutating and CONFIRM and not confirm:
        return _confirm_preview(name, arguments, effective_login)

    ctx_token = client_login_var.set(req_login or "")
    try:
        return await _dispatch(name, arguments)
    finally:
        client_login_var.reset(ctx_token)


async def _dispatch(name: str, arguments: dict):
    async with httpx.AsyncClient() as client:
        try:
            if name == "yd_campaigns_get":
                return await _handle_campaigns_get(client, arguments)
            elif name == "yd_campaigns_add":
                return await _handle_campaigns_add(client, arguments)
            elif name == "yd_campaigns_update":
                return await _handle_campaigns_update(client, arguments)
            elif name == "yd_campaigns_action":
                return await _handle_campaigns_action(client, arguments)
            elif name == "yd_adgroups_add":
                return await _handle_adgroups_add(client, arguments)
            elif name == "yd_adgroups_get":
                return await _handle_adgroups_get(client, arguments)
            elif name == "yd_ads_add":
                return await _handle_ads_add(client, arguments)
            elif name == "yd_ads_update":
                return await _handle_ads_update(client, arguments)
            elif name == "yd_ads_get":
                return await _handle_ads_get(client, arguments)
            elif name == "yd_ads_action":
                return await _handle_ads_action(client, arguments)
            elif name == "yd_keywords_add":
                return await _handle_keywords_add(client, arguments)
            elif name == "yd_keywords_get":
                return await _handle_keywords_get(client, arguments)
            elif name == "yd_keywords_research":
                return await _handle_keywords_research(client, arguments)
            elif name == "yd_bids_set":
                return await _handle_bids_set(client, arguments)
            elif name == "yd_report":
                return await _handle_report(client, arguments)
            elif name == "yd_dictionaries":
                return await _handle_dictionaries(client, arguments)
            elif name == "yd_keywords_has_volume":
                return await _handle_keywords_has_volume(client, arguments)
            elif name == "yd_keyword_bids_get":
                return await _handle_keyword_bids_get(client, arguments)
            elif name == "yd_keyword_bids_set":
                return await _handle_keyword_bids_set(client, arguments)
            elif name == "yd_keyword_bids_set_auto":
                return await _handle_keyword_bids_set_auto(client, arguments)
            elif name == "yd_bid_modifiers_add":
                return await _handle_bid_modifiers_add(client, arguments)
            elif name == "yd_bid_modifiers_get":
                return await _handle_bid_modifiers_get(client, arguments)
            elif name == "yd_bid_modifiers_set":
                return await _handle_bid_modifiers_set(client, arguments)
            elif name == "yd_bid_modifiers_delete":
                return await _handle_bid_modifiers_delete(client, arguments)
            elif name == "yd_negative_keywords_sets_add":
                return await _handle_neg_kw_sets_add(client, arguments)
            elif name == "yd_negative_keywords_sets_get":
                return await _handle_neg_kw_sets_get(client, arguments)
            elif name == "yd_negative_keywords_sets_update":
                return await _handle_neg_kw_sets_update(client, arguments)
            elif name == "yd_negative_keywords_sets_delete":
                return await _handle_neg_kw_sets_delete(client, arguments)
            elif name == "yd_sitelinks_add":
                return await _handle_sitelinks_add(client, arguments)
            elif name == "yd_sitelinks_get":
                return await _handle_sitelinks_get(client, arguments)
            elif name == "yd_sitelinks_delete":
                return await _handle_sitelinks_delete(client, arguments)
            elif name == "yd_ad_extensions_add":
                return await _handle_ad_extensions_add(client, arguments)
            elif name == "yd_ad_extensions_get":
                return await _handle_ad_extensions_get(client, arguments)
            elif name == "yd_ad_extensions_delete":
                return await _handle_ad_extensions_delete(client, arguments)
            elif name == "yd_changes_check":
                return await _handle_changes_check(client, arguments)
            elif name == "yd_audience_targets_add":
                return await _handle_audience_targets_add(client, arguments)
            elif name == "yd_audience_targets_get":
                return await _handle_audience_targets_get(client, arguments)
            elif name == "yd_audience_targets_delete":
                return await _handle_audience_targets_delete(client, arguments)
            elif name == "yd_retargeting_lists_add":
                return await _handle_retargeting_lists_add(client, arguments)
            elif name == "yd_retargeting_lists_get":
                return await _handle_retargeting_lists_get(client, arguments)
            elif name == "yd_retargeting_lists_delete":
                return await _handle_retargeting_lists_delete(client, arguments)
            elif name == "yd_ad_images_add":
                return await _handle_ad_images_add(client, arguments)
            elif name == "yd_ad_images_get":
                return await _handle_ad_images_get(client, arguments)
            elif name == "yd_ad_images_delete":
                return await _handle_ad_images_delete(client, arguments)
            elif name == "yd_businesses_get":
                return await _handle_businesses_get(client, arguments)
            elif name == "yd_direct_accounts_get":
                return await _handle_direct_accounts_get(client, arguments)
            elif name == "yd_clients_get":
                return await _handle_clients_get(client, arguments)
            elif name == "yd_wordstat_top_requests":
                return await _handle_wordstat_top_requests(client, arguments)
            elif name == "yd_wordstat_dynamics":
                return await _handle_wordstat_dynamics(client, arguments)
            elif name == "yd_wordstat_regions":
                return await _handle_wordstat_regions(client, arguments)
            elif name == "yd_wordstat_regions_tree":
                return await _handle_wordstat_regions_tree(client, arguments)
            elif name in _extra_dispatch:
                return await _extra_dispatch[name](client, arguments, _extra_config)
            elif name in _metrika_dispatch:
                return await _metrika_dispatch[name](client, arguments)
            elif name in _audience_dispatch:
                return await _audience_dispatch[name](client, arguments)
            else:
                return _result({"error": f"Unknown tool: {name}"})
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return [TextContent(type="text", text=f"Error: {e}")]


async def _handle_campaigns_get(client, args):
    criteria = {}
    if ids := args.get("ids"):
        criteria["Ids"] = ids
    if types := args.get("types"):
        criteria["Types"] = types
    if states := args.get("states"):
        criteria["States"] = states
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["Id", "Name", "Status", "State", "Type", "StartDate", "DailyBudget", "Statistics"],
    }
    data = await _api(client, "campaigns", "get", params)
    return _result(data.get("result", data))


async def _handle_campaigns_add(client, args):
    campaign = {
        "Name": args["name"],
        "StartDate": args["start_date"],
    }
    if end_date := args.get("end_date"):
        campaign["EndDate"] = end_date
    if args.get("daily_budget_amount"):
        campaign["DailyBudget"] = {
            "Amount": _rubles_to_micros(args["daily_budget_amount"]),
            "Mode": args.get("daily_budget_mode", "DISTRIBUTED"),
        }
    if neg := args.get("negative_keywords"):
        campaign["NegativeKeywords"] = {"Items": neg}

    weekly_limit = _rubles_to_micros(args["weekly_spend_limit"]) if args.get("weekly_spend_limit") else None
    goal_id = args.get("goal_id")
    goal_cpa = _rubles_to_micros(args["goal_cpa"]) if args.get("goal_cpa") else None

    # Build search strategy
    search_strategy = args.get("strategy_search", "WB_MAXIMUM_CLICKS")
    search_obj = {"BiddingStrategyType": search_strategy}

    if search_strategy == "WB_MAXIMUM_CLICKS":
        params = {}
        if weekly_limit:
            params["WeeklySpendLimit"] = weekly_limit
        else:
            params["WeeklySpendLimit"] = _rubles_to_micros(args.get("daily_budget_amount", 300) * 7)
        search_obj["WbMaximumClicks"] = params

    elif search_strategy == "PAY_FOR_CONVERSION":
        params = {}
        if goal_id:
            params["GoalId"] = goal_id
        if goal_cpa:
            params["Cpa"] = goal_cpa
        if weekly_limit:
            params["WeeklySpendLimit"] = weekly_limit
        search_obj["PayForConversion"] = params

    elif search_strategy == "PAY_FOR_CONVERSION_MULTIPLE_GOALS":
        params = {}
        if weekly_limit:
            params["WeeklySpendLimit"] = weekly_limit
        search_obj["PayForConversionMultipleGoals"] = params

    elif search_strategy == "WB_MAXIMUM_CONVERSION_RATE":
        params = {}
        if weekly_limit:
            params["WeeklySpendLimit"] = weekly_limit
        if goal_id:
            params["GoalId"] = goal_id
        search_obj["WbMaximumConversionRate"] = params

    elif search_strategy == "AVERAGE_CPA":
        params = {}
        if goal_id:
            params["GoalId"] = goal_id
        if goal_cpa:
            params["AverageCpa"] = goal_cpa
        if weekly_limit:
            params["WeeklySpendLimit"] = weekly_limit
        search_obj["AverageCpa"] = params

    elif search_strategy == "AVERAGE_CPC":
        params = {}
        if weekly_limit:
            params["WeeklySpendLimit"] = weekly_limit
        search_obj["AverageCpc"] = params

    # Build network strategy
    network_strategy = args.get("strategy_network", "SERVING_OFF")
    network_obj = {"BiddingStrategyType": network_strategy}

    if network_strategy == "WB_MAXIMUM_CONVERSION_RATE":
        params = {}
        if weekly_limit:
            params["WeeklySpendLimit"] = weekly_limit
        if goal_id:
            params["GoalId"] = goal_id
        network_obj["WbMaximumConversionRate"] = params

    elif network_strategy == "WB_MAXIMUM_CLICKS":
        params = {}
        if weekly_limit:
            params["WeeklySpendLimit"] = weekly_limit
        network_obj["WbMaximumClicks"] = params

    elif network_strategy == "NETWORK_DEFAULT":
        network_obj["NetworkDefault"] = {"LimitPercent": 100}

    # Build TextCampaign
    text_campaign = {
        "BiddingStrategy": {
            "Search": search_obj,
            "Network": network_obj,
        },
    }

    # Counter IDs
    if counter_ids := args.get("counter_ids"):
        text_campaign["CounterIds"] = {"Items": counter_ids}

    # Priority goals
    if priority_goals := args.get("priority_goals"):
        text_campaign["PriorityGoals"] = {
            "Items": [{"GoalId": g["goal_id"], "Value": _rubles_to_micros(g["value"])} for g in priority_goals]
        }

    # Settings
    text_campaign["Settings"] = [
        {"Option": "ADD_METRICA_TAG", "Value": "YES"},
        {"Option": "ENABLE_SITE_MONITORING", "Value": "YES"},
    ]

    campaign["TextCampaign"] = text_campaign
    data = await _api(client, "campaigns", "add", {"Campaigns": [campaign]})
    return _result(data.get("result", data))


async def _handle_campaigns_update(client, args):
    campaign = {"Id": args["campaign_id"]}
    if name := args.get("name"):
        campaign["Name"] = name
    if args.get("daily_budget_amount"):
        campaign["DailyBudget"] = {
            "Amount": _rubles_to_micros(args["daily_budget_amount"]),
            "Mode": args.get("daily_budget_mode", "DISTRIBUTED"),
        }
    if neg := args.get("negative_keywords"):
        campaign["NegativeKeywords"] = {"Items": neg}
    if end_date := args.get("end_date"):
        campaign["EndDate"] = end_date
    data = await _api(client, "campaigns", "update", {"Campaigns": [campaign]})
    return _result(data.get("result", data))


async def _handle_campaigns_action(client, args):
    action = args["action"]
    params = {"SelectionCriteria": {"Ids": args["campaign_ids"]}}
    data = await _api(client, "campaigns", action, params)
    return _result(data.get("result", data))


async def _handle_adgroups_add(client, args):
    groups = []
    for g in args["groups"]:
        group = {
            "Name": g["name"],
            "CampaignId": args["campaign_id"],
            "RegionIds": g["region_ids"],
        }
        if neg := g.get("negative_keywords"):
            group["NegativeKeywords"] = {"Items": neg}
        groups.append(group)
    data = await _api(client, "adgroups", "add", {"AdGroups": groups})
    return _result(data.get("result", data))


async def _handle_adgroups_get(client, args):
    criteria = {}
    if ids := args.get("campaign_ids"):
        criteria["CampaignIds"] = ids
    if ids := args.get("group_ids"):
        criteria["Ids"] = ids
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["Id", "Name", "CampaignId", "Status", "RegionIds", "NegativeKeywords"],
    }
    data = await _api(client, "adgroups", "get", params)
    return _result(data.get("result", data))


async def _handle_ads_add(client, args):
    ads = []
    for a in args["ads"]:
        ad = {
            "AdGroupId": a["ad_group_id"],
            "TextAd": {
                "Title": a["title"],
                "Text": a["text"],
                "Href": a["href"],
                "Mobile": a.get("mobile", "NO"),
            },
        }
        if title2 := a.get("title2"):
            ad["TextAd"]["Title2"] = title2
        if sitelink_id := a.get("sitelink_set_id"):
            ad["TextAd"]["SitelinkSetId"] = sitelink_id
        if img_hash := a.get("ad_image_hash"):
            ad["TextAd"]["AdImageHash"] = img_hash
        ads.append(ad)
    data = await _api(client, "ads", "add", {"Ads": ads})
    return _result(data.get("result", data))


async def _handle_ads_update(client, args):
    ads = []
    for a in args["ads"]:
        ad = {"Id": a["id"], "TextAd": {}}
        if title := a.get("title"):
            ad["TextAd"]["Title"] = title
        if title2 := a.get("title2"):
            ad["TextAd"]["Title2"] = title2
        if text := a.get("text"):
            ad["TextAd"]["Text"] = text
        if href := a.get("href"):
            ad["TextAd"]["Href"] = href
        if sitelink_id := a.get("sitelink_set_id"):
            ad["TextAd"]["SitelinkSetId"] = sitelink_id
        if img_hash := a.get("ad_image_hash"):
            ad["TextAd"]["AdImageHash"] = img_hash
        ads.append(ad)
    data = await _api(client, "ads", "update", {"Ads": ads})
    return _result(data.get("result", data))


async def _handle_ads_get(client, args):
    criteria = {}
    if ids := args.get("campaign_ids"):
        criteria["CampaignIds"] = ids
    if ids := args.get("ad_group_ids"):
        criteria["AdGroupIds"] = ids
    if ids := args.get("ad_ids"):
        criteria["Ids"] = ids
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["Id", "AdGroupId", "CampaignId", "Status", "State", "Type"],
        "TextAdFieldNames": ["Title", "Title2", "Text", "Href", "Mobile"],
    }
    data = await _api(client, "ads", "get", params)
    return _result(data.get("result", data))


async def _handle_ads_action(client, args):
    action = args["action"]
    params = {"SelectionCriteria": {"Ids": args["ad_ids"]}}
    data = await _api(client, "ads", action, params)
    return _result(data.get("result", data))


async def _handle_keywords_add(client, args):
    keywords = []
    for kw in args["keywords"]:
        item = {
            "Keyword": kw["keyword"],
            "AdGroupId": kw["ad_group_id"],
        }
        if bid := kw.get("bid"):
            item["Bid"] = _rubles_to_micros(bid)
        keywords.append(item)
    data = await _api(client, "keywords", "add", {"Keywords": keywords})
    return _result(data.get("result", data))


async def _handle_keywords_get(client, args):
    criteria = {}
    if ids := args.get("campaign_ids"):
        criteria["CampaignIds"] = ids
    if ids := args.get("ad_group_ids"):
        criteria["AdGroupIds"] = ids
    if ids := args.get("keyword_ids"):
        criteria["Ids"] = ids
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["Id", "Keyword", "AdGroupId", "CampaignId", "Status", "State", "Bid"],
    }
    data = await _api(client, "keywords", "get", params)
    return _result(data.get("result", data))


async def _handle_keywords_research(client, args):
    keywords = [{"Keyword": kw} for kw in args["keywords"]]
    operations = args.get("operations", ["MERGE_DUPLICATES", "ELIMINATE_OVERLAPPING"])
    params = {
        "Keywords": keywords,
        "Operation": operations,
    }
    data = await _api(client, "keywordsresearch", "deduplicate", params)
    return _result(data.get("result", data))


async def _handle_bids_set(client, args):
    bids = []
    for b in args["bids"]:
        item = {"KeywordId": b["keyword_id"]}
        if bid := b.get("search_bid"):
            item["SearchBid"] = _rubles_to_micros(bid)
        if bid := b.get("network_bid"):
            item["NetworkBid"] = _rubles_to_micros(bid)
        bids.append(item)
    data = await _api(client, "bids", "set", {"Bids": bids})
    return _result(data.get("result", data))


async def _handle_report(client, args):
    """Reports use a different endpoint and flow."""
    url = f"{_base_url()}/reports"
    body = {
        "params": {
            "SelectionCriteria": {
                "DateFrom": args["date_from"],
                "DateTo": args["date_to"],
            },
            "FieldNames": args["field_names"],
            "ReportName": f"report_{args['date_from']}_{args['date_to']}",
            "ReportType": args["report_type"],
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
            "IncludeDiscount": "NO",
        }
    }
    if campaign_ids := args.get("campaign_ids"):
        body["params"]["SelectionCriteria"]["Filter"] = [
            {"Field": "CampaignId", "Operator": "IN", "Values": [str(i) for i in campaign_ids]}
        ]

    headers = _headers()
    headers["processingMode"] = "auto"
    headers["returnMoneyInMicros"] = "false"

    # Reports can be async — poll until ready
    for attempt in range(30):
        resp = await client.post(url, headers=headers, json=body, timeout=120)
        if resp.status_code == 200:
            return [TextContent(type="text", text=resp.text)]
        elif resp.status_code == 201:
            # Offline report created, poll
            retry_in = int(resp.headers.get("retryIn", 5))
            await asyncio.sleep(retry_in)
            continue
        elif resp.status_code == 202:
            # Still processing
            retry_in = int(resp.headers.get("retryIn", 5))
            await asyncio.sleep(retry_in)
            continue
        else:
            return [TextContent(type="text", text=f"Report error {resp.status_code}: {resp.text}")]
    return [TextContent(type="text", text="Report timeout after 30 attempts")]


async def _handle_dictionaries(client, args):
    data = await _api(client, "dictionaries", "get", {"DictionaryNames": args["names"]})
    return _result(data.get("result", data))


# ── hasSearchVolume ────────────────────────────────────────────────────

async def _handle_keywords_has_volume(client, args):
    params = {
        "SelectionCriteria": {
            "Keywords": args["keywords"],
            "RegionIds": args["region_ids"],
        },
        "FieldNames": ["Keyword", "RegionIds", "AllDevices", "MobilePhones", "Tablets", "Desktops"],
    }
    data = await _api(client, "keywordsresearch", "hasSearchVolume", params)
    return _result(data.get("result", data))


# ── KeywordBids ────────────────────────────────────────────────────────

async def _handle_keyword_bids_get(client, args):
    criteria = {}
    if ids := args.get("campaign_ids"):
        criteria["CampaignIds"] = ids
    if ids := args.get("ad_group_ids"):
        criteria["AdGroupIds"] = ids
    if ids := args.get("keyword_ids"):
        criteria["Ids"] = ids
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["KeywordId", "AdGroupId", "CampaignId", "SearchBid", "NetworkBid", "CurrentSearchPrice", "MinSearchPrice"],
    }
    data = await _api(client, "keywordbids", "get", params)
    return _result(data.get("result", data))


async def _handle_keyword_bids_set(client, args):
    bids = []
    for b in args["bids"]:
        item = {"KeywordId": b["keyword_id"]}
        if bid := b.get("search_bid"):
            item["SearchBid"] = _rubles_to_micros(bid)
        if bid := b.get("network_bid"):
            item["NetworkBid"] = _rubles_to_micros(bid)
        bids.append(item)
    data = await _api(client, "keywordbids", "set", {"KeywordBids": bids})
    return _result(data.get("result", data))


async def _handle_keyword_bids_set_auto(client, args):
    bids = []
    for b in args["bids"]:
        item = {"KeywordId": b["keyword_id"]}
        auto = {}
        if scope := b.get("scope"):
            auto["Scope"] = [scope]
        if pos := b.get("position"):
            auto["Position"] = pos
        if mb := b.get("max_bid"):
            auto["MaxBid"] = _rubles_to_micros(mb)
        if inc := b.get("increase_percent"):
            auto["IncreasePercent"] = inc
        item["SearchAutoStrategy"] = auto
        bids.append(item)
    data = await _api(client, "keywordbids", "setAuto", {"KeywordBids": bids})
    return _result(data.get("result", data))


# ── BidModifiers ───────────────────────────────────────────────────────

async def _handle_bid_modifiers_add(client, args):
    modifier = {}
    if cid := args.get("campaign_id"):
        modifier["CampaignId"] = cid
    if gid := args.get("ad_group_id"):
        modifier["AdGroupId"] = gid
    if mob := args.get("mobile_adjustment"):
        modifier["MobileAdjustment"] = {"BidModifier": mob}
    if desk := args.get("desktop_adjustment"):
        modifier["DesktopAdjustment"] = {"BidModifier": desk}
    if tab := args.get("tablet_adjustment"):
        modifier["TabletAdjustment"] = {"BidModifier": tab}
    if demos := args.get("demographics"):
        modifier["DemographicsAdjustments"] = [
            {k: v for k, v in [
                ("Gender", d.get("gender")),
                ("Age", d.get("age")),
                ("BidModifier", d["bid_modifier"]),
            ] if v is not None}
            for d in demos
        ]
    if regs := args.get("regional"):
        modifier["RegionalAdjustments"] = [
            {"RegionId": r["region_id"], "BidModifier": r["bid_modifier"]}
            for r in regs
        ]
    data = await _api(client, "bidmodifiers", "add", {"BidModifiers": [modifier]})
    return _result(data.get("result", data))


async def _handle_bid_modifiers_get(client, args):
    criteria = {}
    if ids := args.get("campaign_ids"):
        criteria["CampaignIds"] = ids
    if ids := args.get("ad_group_ids"):
        criteria["AdGroupIds"] = ids
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type",
                        "MobileAdjustment", "DesktopAdjustment", "TabletAdjustment",
                        "DemographicsAdjustments", "RegionalAdjustments"],
    }
    data = await _api(client, "bidmodifiers", "get", params)
    return _result(data.get("result", data))


async def _handle_bid_modifiers_set(client, args):
    mods = [{"Id": m["id"], "BidModifier": m["bid_modifier"]} for m in args["modifiers"]]
    data = await _api(client, "bidmodifiers", "set", {"BidModifiers": mods})
    return _result(data.get("result", data))


async def _handle_bid_modifiers_delete(client, args):
    data = await _api(client, "bidmodifiers", "delete", {"SelectionCriteria": {"Ids": args["ids"]}})
    return _result(data.get("result", data))


# ── NegativeKeywordSharedSets ──────────────────────────────────────────

async def _handle_neg_kw_sets_add(client, args):
    item = {
        "Name": args["name"],
        "NegativeKeywords": args["negative_keywords"],
    }
    data = await _api(client, "negativekeywordsharedsets", "add", {"NegativeKeywordSharedSets": [item]})
    return _result(data.get("result", data))


async def _handle_neg_kw_sets_get(client, args):
    criteria = {}
    if ids := args.get("ids"):
        criteria["Ids"] = ids
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["Id", "Name", "NegativeKeywords"],
    }
    data = await _api(client, "negativekeywordsharedsets", "get", params)
    return _result(data.get("result", data))


async def _handle_neg_kw_sets_update(client, args):
    item = {"Id": args["id"]}
    if name := args.get("name"):
        item["Name"] = name
    if nk := args.get("negative_keywords"):
        item["NegativeKeywords"] = nk
    data = await _api(client, "negativekeywordsharedsets", "update", {"NegativeKeywordSharedSets": [item]})
    return _result(data.get("result", data))


async def _handle_neg_kw_sets_delete(client, args):
    data = await _api(client, "negativekeywordsharedsets", "delete", {"SelectionCriteria": {"Ids": args["ids"]}})
    return _result(data.get("result", data))


# ── Sitelinks ──────────────────────────────────────────────────────────

async def _handle_sitelinks_add(client, args):
    sitelinks = []
    for s in args["sitelinks"]:
        sl = {"Title": s["title"], "Href": s["href"]}
        if desc := s.get("description"):
            sl["Description"] = desc
        sitelinks.append(sl)
    data = await _api(client, "sitelinks", "add", {"SitelinksSets": [{"Sitelinks": sitelinks}]})
    return _result(data.get("result", data))


async def _handle_sitelinks_get(client, args):
    params = {
        "SelectionCriteria": {"Ids": args["ids"]},
        "FieldNames": ["Id", "Sitelinks"],
    }
    data = await _api(client, "sitelinks", "get", params)
    return _result(data.get("result", data))


async def _handle_sitelinks_delete(client, args):
    data = await _api(client, "sitelinks", "delete", {"SelectionCriteria": {"Ids": args["ids"]}})
    return _result(data.get("result", data))


# ── AdExtensions (Callouts) ───────────────────────────────────────────

async def _handle_ad_extensions_add(client, args):
    extensions = [{"Callout": {"CalloutText": text}} for text in args["callouts"]]
    data = await _api(client, "adextensions", "add", {"AdExtensions": extensions})
    return _result(data.get("result", data))


async def _handle_ad_extensions_get(client, args):
    params = {
        "SelectionCriteria": {"Ids": args["ids"]},
        "FieldNames": ["Id", "Type", "Callout", "Status"],
    }
    data = await _api(client, "adextensions", "get", params)
    return _result(data.get("result", data))


async def _handle_ad_extensions_delete(client, args):
    data = await _api(client, "adextensions", "delete", {"SelectionCriteria": {"Ids": args["ids"]}})
    return _result(data.get("result", data))


# ── Changes ────────────────────────────────────────────────────────────

async def _handle_changes_check(client, args):
    params = {
        "Timestamp": args["timestamp"],
        "FieldNames": args["field_names"],
    }
    if ids := args.get("campaign_ids"):
        params["CampaignIds"] = ids
    data = await _api(client, "changes", "check", params)
    return _result(data.get("result", data))


# ── AudienceTargets ───────────────────────────────────────────────────

async def _handle_audience_targets_add(client, args):
    targets = []
    for t in args["targets"]:
        item = {"AdGroupId": t["ad_group_id"]}
        if rl := t.get("retargeting_list_id"):
            item["RetargetingListId"] = rl
        if ii := t.get("interest_id"):
            item["InterestId"] = ii
        if cb := t.get("context_bid"):
            item["ContextBid"] = _rubles_to_micros(cb)
        if sp := t.get("strategy_priority"):
            item["StrategyPriority"] = sp
        targets.append(item)
    data = await _api(client, "audiencetargets", "add", {"AudienceTargets": targets})
    return _result(data.get("result", data))


async def _handle_audience_targets_get(client, args):
    criteria = {}
    if ids := args.get("campaign_ids"):
        criteria["CampaignIds"] = ids
    if ids := args.get("ad_group_ids"):
        criteria["AdGroupIds"] = ids
    if ids := args.get("ids"):
        criteria["Ids"] = ids
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["Id", "AdGroupId", "CampaignId", "RetargetingListId", "InterestId", "ContextBid", "StrategyPriority", "State"],
    }
    data = await _api(client, "audiencetargets", "get", params)
    return _result(data.get("result", data))


async def _handle_audience_targets_delete(client, args):
    data = await _api(client, "audiencetargets", "delete", {"SelectionCriteria": {"Ids": args["ids"]}})
    return _result(data.get("result", data))


# ── RetargetingLists ──────────────────────────────────────────────────

_AUDIENCE_SEGMENT_ID_OFFSET = 2_000_000_000  # Direct encodes Audience segments as id + 2e9


async def _handle_retargeting_lists_add(client, args):
    rules = []
    for r in args["rules"]:
        arguments = []
        for g in r["goals"]:
            if g.get("audience_segment_id") is not None:
                arg = {"ExternalId": _AUDIENCE_SEGMENT_ID_OFFSET + g["audience_segment_id"]}
            else:
                arg = {"ExternalId": g["goal_id"]}
            if g.get("membership_life_span") is not None:
                arg["MembershipLifeSpan"] = g["membership_life_span"]
            arguments.append(arg)
        rules.append({"Operator": r["operator"], "Arguments": arguments})
    item = {
        "Name": args["name"],
        "Rules": rules,
    }
    if t := args.get("type"):
        item["Type"] = t
    if desc := args.get("description"):
        item["Description"] = desc
    data = await _api(client, "retargetinglists", "add", {"RetargetingLists": [item]})
    return _result(data.get("result", data))


async def _handle_retargeting_lists_get(client, args):
    criteria = {}
    if ids := args.get("ids"):
        criteria["Ids"] = ids
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["Id", "Name", "Description", "Rules", "Type", "IsAvailable"],
    }
    data = await _api(client, "retargetinglists", "get", params)
    return _result(data.get("result", data))


async def _handle_retargeting_lists_delete(client, args):
    data = await _api(client, "retargetinglists", "delete", {"SelectionCriteria": {"Ids": args["ids"]}})
    return _result(data.get("result", data))


# ── AdImages ──────────────────────────────────────────────────────────

async def _handle_ad_images_add(client, args):
    images = [{"ImageData": img["image_data"], "Name": img["name"]} for img in args["images"]]
    data = await _api(client, "adimages", "add", {"AdImages": images})
    return _result(data.get("result", data))


async def _handle_ad_images_get(client, args):
    criteria = {}
    if ids := args.get("ids"):
        criteria["AdImageHashes"] = ids
    if args.get("associated"):
        criteria["Associated"] = "YES"
    params = {
        "SelectionCriteria": criteria,
        "FieldNames": ["AdImageHash", "Name", "Type", "Subtype", "OriginalUrl"],
    }
    data = await _api(client, "adimages", "get", params)
    return _result(data.get("result", data))


async def _handle_ad_images_delete(client, args):
    data = await _api(client, "adimages", "delete", {"SelectionCriteria": {"AdImageHashes": args["ids"]}})
    return _result(data.get("result", data))


# ── Businesses ────────────────────────────────────────────────────────

async def _handle_businesses_get(client, args):
    params = {
        "SelectionCriteria": {"Ids": args["ids"]},
        "FieldNames": ["Id", "Name", "Address", "Phone", "ProfileUrl", "IsPublished"],
    }
    data = await _api(client, "businesses", "get", params)
    return _result(data.get("result", data))


# ── Clients ───────────────────────────────────────────────────────────

async def _handle_direct_accounts_get(client, args):
    """Probe every configured Direct token with Clients.get (ignores client_login)."""
    accounts = []
    for source, token in [("YD_OAUTH_TOKEN", TOKEN)] + list(DIRECT_TOKENS.items()):
        headers = {"Authorization": f"Bearer {token}", "Accept-Language": "ru",
                   "Content-Type": "application/json"}
        if source == "YD_OAUTH_TOKEN" and LOGIN and LOGIN not in DIRECT_TOKENS:
            headers["Client-Login"] = LOGIN
        entry = {"configured_as": source}
        try:
            resp = await request_with_retry(
                client, f"{_base_url()}/clients", headers=headers, timeout=60,
                json_body={"method": "get", "params": {"FieldNames": ["Login", "ClientId", "Currency", "Type"]}})
            data = resp.json()
            if "error" in data:
                entry["error"] = data["error"].get("error_detail") or data["error"].get("error_string")
            else:
                c = (data.get("result", {}).get("Clients") or [{}])[0]
                entry.update({"login": c.get("Login"), "client_id": c.get("ClientId"),
                              "currency": c.get("Currency"), "type": c.get("Type")})
        except Exception as e:  # network / non-JSON
            entry["error"] = str(e)[:200]
        accounts.append(entry)
    return _result({"accounts": accounts, "default_login": LOGIN or None})


async def _handle_clients_get(client, args):
    params = {
        "FieldNames": ["Login", "ClientId", "CountryId", "Currency", "ClientInfo",
                        "Notification", "Phone", "Representatives", "Restrictions",
                        "Settings", "AccountQuality"],
    }
    data = await _api(client, "clients", "get", params)
    return _result(data.get("result", data))


# ── Wordstat API ──────────────────────────────────────────────────────

_iam_token_cache = {"token": "", "expires": 0}


async def _get_iam_token(client: httpx.AsyncClient) -> str:
    """Get IAM token from OAuth, with caching (tokens expire in ~12h)."""
    import time
    now = time.time()
    if _iam_token_cache["token"] and _iam_token_cache["expires"] > now + 300:
        return _iam_token_cache["token"]
    resp = await client.post(
        "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        json={"yandexPassportOauthToken": TOKEN},
        timeout=30,
    )
    data = resp.json()
    if "iamToken" not in data:
        raise Exception(f"Failed to get IAM token: {data}")
    _iam_token_cache["token"] = data["iamToken"]
    _iam_token_cache["expires"] = iam_expiry(data, now)
    logger.debug("Got new IAM token (expiresAt=%s)", data.get("expiresAt"))
    return _iam_token_cache["token"]


async def _wordstat_request(client: httpx.AsyncClient, endpoint: str, body: dict) -> dict:
    """Call Wordstat API via Yandex Cloud Search API."""
    url = f"{WORDSTAT_URL}{endpoint}"
    if YC_API_KEY:
        auth = f"Api-Key {YC_API_KEY}"
    else:
        auth = f"Bearer {await _get_iam_token(client)}"
    body["folderId"] = YC_FOLDER_ID
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
    }
    _log_body("WORDSTAT REQUEST %s: %s", url, json.dumps(body, ensure_ascii=False)[:2000])
    resp = await client.post(url, headers=headers, json=body, timeout=60)
    _log_body("WORDSTAT RESPONSE %s: %s", resp.status_code, resp.text[:2000])
    if resp.status_code == 429:
        raise Exception(f"Wordstat rate limit exceeded (429). Retry later.")
    if resp.status_code == 503:
        raise Exception(f"Wordstat global quota exceeded (503).")
    if resp.status_code != 200:
        raise Exception(f"Wordstat error {resp.status_code}: {resp.text[:500]}")
    return resp.json()


async def _handle_wordstat_top_requests(client, args):
    # The Wordstat /topRequests endpoint requires numPhrases (1..2000).
    num = int(args.get("num_phrases", 30))
    num = max(1, min(num, 2000))
    body = {"phrase": args["phrase"], "numPhrases": num}
    if regions := args.get("regions"):
        body["regions"] = regions
    if devices := args.get("devices"):
        body["devices"] = devices
    data = await _wordstat_request(client, "/topRequests", body)
    return _result(data)


def _rfc3339(d: str) -> str:
    """Wordstat /dynamics wants protobuf Timestamps; accept a plain YYYY-MM-DD too."""
    d = (d or "").strip()
    return d if "T" in d else f"{d}T00:00:00Z"


_WORDSTAT_PERIODS = {
    "monthly": "PERIOD_MONTHLY", "weekly": "PERIOD_WEEKLY", "daily": "PERIOD_DAILY",
    "PERIOD_MONTHLY": "PERIOD_MONTHLY", "PERIOD_WEEKLY": "PERIOD_WEEKLY", "PERIOD_DAILY": "PERIOD_DAILY",
}


async def _handle_wordstat_dynamics(client, args):
    period = _WORDSTAT_PERIODS.get(args["period"], args["period"])
    body = {
        "phrase": args["phrase"],
        "period": period,
        "fromDate": _rfc3339(args["from_date"]),
        "toDate": _rfc3339(args["to_date"]),
    }
    if regions := args.get("regions"):
        body["regions"] = regions
    if devices := args.get("devices"):
        body["devices"] = devices
    data = await _wordstat_request(client, "/dynamics", body)
    return _result(data)


async def _handle_wordstat_regions(client, args):
    body = {"phrase": args["phrase"]}
    if rt := args.get("region_type"):
        body["regionType"] = rt
    if devices := args.get("devices"):
        body["devices"] = devices
    data = await _wordstat_request(client, "/regions", body)
    return _result(data)


async def _handle_wordstat_regions_tree(client, args):
    data = await _wordstat_request(client, "/getRegionsTree", {})
    return _result(data)


# ── Register extra modules ─────────────────────────────────────────────

_metrika_dispatch = {}
register_metrika_handlers(_metrika_dispatch, METRIKA_TOKEN)

_audience_dispatch = {}
register_audience_handlers(_audience_dispatch, AUDIENCE_TOKEN)

_extra_dispatch = {}
register_extra_direct_handlers(_extra_dispatch)
_extra_config = {
    "token": TOKEN,
    "base_url": _base_url(),
    "login": LOGIN,
    "folder_id": YC_FOLDER_ID,
    "yc_api_key": YC_API_KEY,
}


async def main():
    if not TOKEN:
        logger.error("YD_OAUTH_TOKEN environment variable is required")
        sys.exit(1)
    logger.info("Starting Yandex Ads MCP server (sandbox=%s, tools=%d)",
                USE_SANDBOX, len(TOOLS) + len(METRIKA_TOOLS) + len(EXTRA_DIRECT_TOOLS) + len(AUDIENCE_TOOLS))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main_sync():
    """Entry point for pyproject.toml console_scripts."""
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
