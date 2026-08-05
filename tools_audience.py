"""Yandex Audience API tools for MCP server. 23 tools total.

API reference: https://yandex.ru/dev/audience/
Base URL: https://api-audience.yandex.ru/v1/management/
Auth: OAuth token; the OAuth app must have the "Яндекс Аудитории" permission
(create/edit + read segments). Without it the API returns 403 access_denied.
"""

import json
import httpx
from mcp.types import Tool, TextContent

BASE_URL = "https://api-audience.yandex.ru/v1/management"


# ── Helpers ───────────────────────────────────────────────────────────

async def _audience_api(client, method, path, token, params=None, body=None, files=None):
    """Call Yandex Audience API."""
    url = f"{BASE_URL}{path}"
    headers = {"Authorization": f"OAuth {token}"}
    kwargs = {"headers": headers, "timeout": 60}
    if params:
        kwargs["params"] = params
    if body is not None:
        kwargs["json"] = body
    if files is not None:
        kwargs["files"] = files
    resp = await getattr(client, method.lower())(url, **kwargs)
    if resp.status_code == 204:
        return {"success": True}
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code >= 400 or (isinstance(data, dict) and data.get("errors")):
        detail = json.dumps(data, ensure_ascii=False)[:500] if data else resp.text[:500]
        hint = ""
        if resp.status_code == 403:
            hint = (" Hint: the OAuth app must have the Yandex Audience permission "
                    "(re-issue the token after adding it at https://oauth.yandex.ru/); "
                    "for multi-account setups set YD_AUDIENCE_TOKEN.")
        raise Exception(f"Audience API error {resp.status_code}: {detail}{hint}")
    return data


def _result(data):
    return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]


def _tool(name, description, properties=None, required=None):
    schema = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return Tool(name=name, description=description, inputSchema=schema)


def _prop_str(desc):
    return {"type": "string", "description": desc}


def _prop_int(desc):
    return {"type": "integer", "description": desc}


def _prop_bool(desc):
    return {"type": "boolean", "description": desc}


def _prop_segment():
    return {"segment_id": _prop_int("Segment ID")}


# ── Tool definitions ──────────────────────────────────────────────────

AUDIENCE_TOOLS = [
    # ── SEGMENTS (11) ────────────────────────────────────────────────
    _tool("yd_audience_segments_get",
          "List Yandex Audience segments available to the user. Optional filter by pixel ID.", {
        "pixel": _prop_int("Optional. Only segments built from this pixel ID"),
    }),
    _tool("yd_audience_segment_upload",
          "Upload user data (emails/phones/device IDs/ClientIDs/CRM rows) to create a draft "
          "Audience segment. Pass identifiers inline via 'content' (one per line) or a local "
          "'file_path'. The file must contain at least 100 records (Yandex recommends 1000+). "
          "Returns a draft segment; save it with yd_audience_segment_confirm.", {
        "content": _prop_str("File content: one identifier per line (or CSV rows when csv=true)"),
        "file_path": _prop_str("Path to a local file to upload (alternative to 'content')"),
        "csv": _prop_bool("true = upload as CSV with header (segments/upload_csv_file); "
                          "false/omit = plain list (segments/upload_file)"),
    }),
    _tool("yd_audience_segment_confirm",
          "Save (confirm) an uploaded draft segment, turning it into a processing segment.", {
        **_prop_segment(),
        "name": _prop_str("Segment name"),
        "content_type": _prop_str("Identifier type: emails | phones | idfa_gaid | client_id | mac | crm"),
        "hashed": _prop_bool("true if uploaded identifiers are md5-hashed"),
    }, ["segment_id", "name", "content_type"]),
    _tool("yd_audience_segment_update", "Rename an Audience segment.", {
        **_prop_segment(),
        "name": _prop_str("New segment name"),
    }, ["segment_id", "name"]),
    _tool("yd_audience_segment_delete", "Delete an Audience segment.", {
        **_prop_segment(),
    }, ["segment_id"]),
    _tool("yd_audience_segment_reprocess",
          "Force reprocessing of an uploaded segment (quota: 2 per segment, 20 per login per 24h).", {
        **_prop_segment(),
    }, ["segment_id"]),
    _tool("yd_audience_segment_create_lookalike",
          "Create a lookalike segment: users similar to an existing segment.", {
        "name": _prop_str("Segment name"),
        "lookalike_link": _prop_int("Source segment ID"),
        "lookalike_value": _prop_int("Precision/coverage: 1 (most similar) .. 5 (widest reach)"),
        "maintain_device_distribution": _prop_bool("Preserve device-type distribution of the source"),
        "maintain_geo_distribution": _prop_bool("Preserve geo distribution of the source"),
    }, ["name", "lookalike_link", "lookalike_value"]),
    _tool("yd_audience_segment_create_metrika",
          "Create a segment from a Yandex Metrika object (counter audience, goal or segment).", {
        "name": _prop_str("Segment name"),
        "metrika_segment_type": _prop_str("Source type: counter_id | goal_id | segment_id"),
        "metrika_segment_id": _prop_int("ID of the counter / goal / Metrika segment"),
    }, ["name", "metrika_segment_type", "metrika_segment_id"]),
    _tool("yd_audience_segment_create_appmetrica",
          "Create a segment from an AppMetrica object (application or segment).", {
        "name": _prop_str("Segment name"),
        "app_metrica_segment_type": _prop_str("Source type: api_key (application) | segment_id"),
        "app_metrica_segment_id": _prop_int("ID of the AppMetrica application / segment"),
    }, ["name", "app_metrica_segment_type", "app_metrica_segment_id"]),
    _tool("yd_audience_segment_create_geo",
          "Create a geolocation segment. Circle mode: pass points + radius. "
          "Polygon mode: pass polygons (radius ignored).", {
        "name": _prop_str("Segment name"),
        "geo_segment_type": _prop_str("condition | regular | home | work "
                                      "(how often users visit the area)"),
        "points": {"type": "array", "items": {"type": "object"},
                   "description": "Circle centers: [{\"latitude\": .., \"longitude\": .., \"description\": \"..\"}]"},
        "radius": _prop_int("Circle radius in meters (500-10000), circle mode only"),
        "polygons": {"type": "array", "items": {"type": "object"},
                     "description": "Polygon mode: [{\"points\": [{\"latitude\": .., \"longitude\": ..}, ...]}] "
                                    "(3-100 vertices each)"},
        "times_quantity": _prop_int("Visits count for type=condition"),
        "period_length": _prop_int("Period in days for type=condition"),
    }, ["name", "geo_segment_type"]),
    _tool("yd_audience_segment_create_pixel",
          "Create a segment from a pixel (users who saw tagged media ads).", {
        "name": _prop_str("Segment name"),
        "pixel_id": _prop_int("Pixel ID"),
        "period_length": _prop_int("Look-back window in days (1-90)"),
        "times_quantity": _prop_int("How many times the user saw the ad"),
        "times_quantity_operation": _prop_str("Comparison for times_quantity: eq | lt | gt"),
        "utm_source": _prop_str("Filter by utm_source"),
        "utm_content": _prop_str("Filter by utm_content"),
        "utm_campaign": _prop_str("Filter by utm_campaign"),
        "utm_term": _prop_str("Filter by utm_term"),
        "utm_medium": _prop_str("Filter by utm_medium"),
    }, ["name", "pixel_id", "period_length"]),

    # ── GRANTS (3) ───────────────────────────────────────────────────
    _tool("yd_audience_grants_get", "List access permissions (grants) of a segment.", {
        **_prop_segment(),
    }, ["segment_id"]),
    _tool("yd_audience_grant_add",
          "Grant a user (login) access to a segment, e.g. to use it in their Direct campaigns.", {
        **_prop_segment(),
        "user_login": _prop_str("Yandex login to grant access to"),
        "comment": _prop_str("Optional comment"),
    }, ["segment_id", "user_login"]),
    _tool("yd_audience_grant_delete", "Revoke a user's access to a segment.", {
        **_prop_segment(),
        "user_login": _prop_str("Yandex login to revoke"),
    }, ["segment_id", "user_login"]),

    # ── PIXELS (5) ───────────────────────────────────────────────────
    _tool("yd_audience_pixels_get", "List Audience pixels (with 7/30/90-day user counts).", {}),
    _tool("yd_audience_pixel_create", "Create an Audience pixel for tracking media ad views.", {
        "name": _prop_str("Pixel name"),
    }, ["name"]),
    _tool("yd_audience_pixel_update", "Rename an Audience pixel.", {
        "pixel_id": _prop_int("Pixel ID"),
        "name": _prop_str("New pixel name"),
    }, ["pixel_id", "name"]),
    _tool("yd_audience_pixel_delete", "Delete an Audience pixel.", {
        "pixel_id": _prop_int("Pixel ID"),
    }, ["pixel_id"]),
    _tool("yd_audience_pixel_undelete", "Restore a deleted Audience pixel.", {
        "pixel_id": _prop_int("Pixel ID"),
    }, ["pixel_id"]),

    # ── ACCOUNTS & DELEGATES (4) ─────────────────────────────────────
    _tool("yd_audience_accounts_get",
          "List accounts where the current user is a delegate (representative).", {}),
    _tool("yd_audience_delegates_get",
          "List delegates (representatives) of the current account.", {}),
    _tool("yd_audience_delegate_add", "Add a delegate to the current account.", {
        "user_login": _prop_str("Yandex login of the delegate"),
        "perm": _prop_str("Permission level: view | edit"),
        "comment": _prop_str("Optional comment"),
    }, ["user_login", "perm"]),
    _tool("yd_audience_delegate_delete", "Remove a delegate from the current account.", {
        "user_login": _prop_str("Yandex login of the delegate"),
    }, ["user_login"]),
]


# ── Handler registration ──────────────────────────────────────────────

def register_audience_handlers(dispatch: dict, token: str):
    """Register all Audience tool handlers into dispatch dict."""

    # ── SEGMENTS ─────────────────────────────────────────────────────

    async def segments_get(client, args, _t=token):
        params = {"pixel": args["pixel"]} if args.get("pixel") else None
        return _result(await _audience_api(client, "get", "/segments", _t, params=params))

    async def segment_upload(client, args, _t=token):
        if args.get("content"):
            payload = args["content"].encode("utf-8")
        elif args.get("file_path"):
            with open(args["file_path"], "rb") as f:
                payload = f.read()
        else:
            return _result({"error": "Provide 'content' or 'file_path'"})
        endpoint = "/segments/upload_csv_file" if args.get("csv") else "/segments/upload_file"
        files = {"file": ("segment_data.csv", payload, "text/csv")}
        return _result(await _audience_api(client, "post", endpoint, _t, files=files))

    async def segment_confirm(client, args, _t=token):
        seg = {
            "id": args["segment_id"],
            "name": args["name"],
            "content_type": args["content_type"],
            "hashed": 1 if args.get("hashed") else 0,
        }
        return _result(await _audience_api(
            client, "post", f"/segment/{args['segment_id']}/confirm", _t, body={"segment": seg}))

    async def segment_update(client, args, _t=token):
        return _result(await _audience_api(
            client, "put", f"/segment/{args['segment_id']}", _t,
            body={"segment": {"id": args["segment_id"], "name": args["name"]}}))

    async def segment_delete(client, args, _t=token):
        return _result(await _audience_api(client, "delete", f"/segment/{args['segment_id']}", _t))

    async def segment_reprocess(client, args, _t=token):
        return _result(await _audience_api(
            client, "put", f"/segment/{args['segment_id']}/reprocess", _t))

    def _create(endpoint, seg):
        async def handler(client, args, _t=token, _e=endpoint, _s=seg):
            return _result(await _audience_api(client, "post", _e, _t, body={"segment": _s(args)}))
        return handler

    def _lookalike_seg(args):
        seg = {
            "name": args["name"],
            "lookalike_link": args["lookalike_link"],
            "lookalike_value": args["lookalike_value"],
        }
        for k in ("maintain_device_distribution", "maintain_geo_distribution"):
            if args.get(k) is not None:
                seg[k] = args[k]
        return seg

    def _metrika_seg(args):
        return {
            "name": args["name"],
            "metrika_segment_type": args["metrika_segment_type"],
            "metrika_segment_id": args["metrika_segment_id"],
        }

    def _appmetrica_seg(args):
        return {
            "name": args["name"],
            "app_metrica_segment_type": args["app_metrica_segment_type"],
            "app_metrica_segment_id": args["app_metrica_segment_id"],
        }

    def _pixel_seg(args):
        seg = {"name": args["name"], "pixel_id": args["pixel_id"],
               "period_length": args["period_length"]}
        for k in ("times_quantity", "times_quantity_operation",
                  "utm_source", "utm_content", "utm_campaign", "utm_term", "utm_medium"):
            if args.get(k) is not None:
                seg[k] = args[k]
        return seg

    async def segment_create_geo(client, args, _t=token):
        seg = {"name": args["name"], "geo_segment_type": args["geo_segment_type"]}
        for k in ("times_quantity", "period_length"):
            if args.get(k) is not None:
                seg[k] = args[k]
        if args.get("polygons"):
            seg["polygons"] = args["polygons"]
            endpoint = "/segments/create_geo_polygon"
        else:
            if not args.get("points"):
                return _result({"error": "Provide 'points' (+ optional radius) or 'polygons'"})
            seg["points"] = args["points"]
            if args.get("radius") is not None:
                seg["radius"] = args["radius"]
            endpoint = "/segments/create_geo"
        return _result(await _audience_api(client, "post", endpoint, _t, body={"segment": seg}))

    # ── GRANTS ───────────────────────────────────────────────────────

    async def grants_get(client, args, _t=token):
        return _result(await _audience_api(
            client, "get", f"/segment/{args['segment_id']}/grants", _t))

    async def grant_add(client, args, _t=token):
        grant = {"user_login": args["user_login"]}
        if args.get("comment"):
            grant["comment"] = args["comment"]
        return _result(await _audience_api(
            client, "put", f"/segment/{args['segment_id']}/grant", _t, body={"grant": grant}))

    async def grant_delete(client, args, _t=token):
        return _result(await _audience_api(
            client, "delete", f"/segment/{args['segment_id']}/grant", _t,
            params={"user_login": args["user_login"]}))

    # ── PIXELS ───────────────────────────────────────────────────────

    async def pixels_get(client, args, _t=token):
        return _result(await _audience_api(client, "get", "/pixels", _t))

    async def pixel_create(client, args, _t=token):
        return _result(await _audience_api(
            client, "post", "/pixels", _t, body={"pixel": {"name": args["name"]}}))

    async def pixel_update(client, args, _t=token):
        return _result(await _audience_api(
            client, "put", f"/pixel/{args['pixel_id']}", _t,
            body={"pixel": {"id": args["pixel_id"], "name": args["name"]}}))

    async def pixel_delete(client, args, _t=token):
        return _result(await _audience_api(client, "delete", f"/pixel/{args['pixel_id']}", _t))

    async def pixel_undelete(client, args, _t=token):
        return _result(await _audience_api(
            client, "post", f"/pixel/{args['pixel_id']}/undelete", _t))

    # ── ACCOUNTS & DELEGATES ─────────────────────────────────────────

    async def accounts_get(client, args, _t=token):
        return _result(await _audience_api(client, "get", "/accounts", _t))

    async def delegates_get(client, args, _t=token):
        return _result(await _audience_api(client, "get", "/delegates", _t))

    async def delegate_add(client, args, _t=token):
        delegate = {"user_login": args["user_login"], "perm": args["perm"]}
        if args.get("comment"):
            delegate["comment"] = args["comment"]
        return _result(await _audience_api(
            client, "put", "/delegate", _t, body={"delegate": delegate}))

    async def delegate_delete(client, args, _t=token):
        return _result(await _audience_api(
            client, "delete", "/delegate", _t, params={"user_login": args["user_login"]}))

    dispatch.update({
        "yd_audience_segments_get": segments_get,
        "yd_audience_segment_upload": segment_upload,
        "yd_audience_segment_confirm": segment_confirm,
        "yd_audience_segment_update": segment_update,
        "yd_audience_segment_delete": segment_delete,
        "yd_audience_segment_reprocess": segment_reprocess,
        "yd_audience_segment_create_lookalike": _create("/segments/create_lookalike", _lookalike_seg),
        "yd_audience_segment_create_metrika": _create("/segments/create_metrika", _metrika_seg),
        "yd_audience_segment_create_appmetrica": _create("/segments/create_appmetrica", _appmetrica_seg),
        "yd_audience_segment_create_geo": segment_create_geo,
        "yd_audience_segment_create_pixel": _create("/segments/create_pixel", _pixel_seg),
        "yd_audience_grants_get": grants_get,
        "yd_audience_grant_add": grant_add,
        "yd_audience_grant_delete": grant_delete,
        "yd_audience_pixels_get": pixels_get,
        "yd_audience_pixel_create": pixel_create,
        "yd_audience_pixel_update": pixel_update,
        "yd_audience_pixel_delete": pixel_delete,
        "yd_audience_pixel_undelete": pixel_undelete,
        "yd_audience_accounts_get": accounts_get,
        "yd_audience_delegates_get": delegates_get,
        "yd_audience_delegate_add": delegate_add,
        "yd_audience_delegate_delete": delegate_delete,
    })
