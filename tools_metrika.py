"""Yandex Metrika tools for MCP server. 43 tools total."""

import json
import httpx
from mcp.types import Tool, TextContent

BASE_URL = "https://api-metrika.yandex.net"


# ── Helpers ───────────────────────────────────────────────────────────

async def _metrika_api(client, method, path, token, params=None, body=None):
    """Call Metrika API."""
    url = f"{BASE_URL}{path}"
    headers = {"Authorization": f"OAuth {token}"}
    kwargs = {"headers": headers, "timeout": 30}
    if params:
        kwargs["params"] = params
    if body:
        kwargs["json"] = body
    resp = await getattr(client, method.lower())(url, **kwargs)
    if resp.status_code == 204:
        return {"success": True}
    if resp.status_code >= 400:
        raise Exception(f"Metrika API error {resp.status_code}: {resp.text}")
    return resp.json()


def _result(data):
    return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]


def _tool(name, description, properties=None, required=None):
    """Shortcut to create a Tool with standard schema."""
    schema = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return Tool(name=name, description=description, inputSchema=schema)


def _prop_counter():
    return {"counter_id": {"type": "integer", "description": "Counter ID"}}


def _prop_str(desc):
    return {"type": "string", "description": desc}


def _prop_int(desc):
    return {"type": "integer", "description": desc}


def _prop_arr_str(desc):
    return {"type": "array", "items": {"type": "string"}, "description": desc}


# ── CRUD helpers ──────────────────────────────────────────────────────

async def _crud_list(client, token, path, params=None):
    return _result(await _metrika_api(client, "get", path, token, params=params))


async def _crud_create(client, token, path, body):
    return _result(await _metrika_api(client, "post", path, token, body=body))


async def _crud_update(client, token, path, body):
    return _result(await _metrika_api(client, "put", path, token, body=body))


async def _crud_delete(client, token, path):
    return _result(await _metrika_api(client, "delete", path, token))


# ── Tool definitions ─────────────────────────────────────────────────

METRIKA_TOOLS = [
    # ── COUNTERS (5) ─────────────────────────────────────────────────
    _tool("yd_metrika_counters_get", "List all Metrika counters. Optional search and favorite filter.", {
        "search_string": _prop_str("Search by name or URL"),
        "favorite": {"type": "boolean", "description": "Only favorite counters"},
    }),
    _tool("yd_metrika_counter_get", "Get counter details by ID.", {
        **_prop_counter(),
    }, ["counter_id"]),
    _tool("yd_metrika_counter_create", "Create a new Metrika counter.", {
        "name": _prop_str("Counter name"),
        "site": _prop_str("Site domain (e.g. example.com)"),
    }, ["name", "site"]),
    _tool("yd_metrika_counter_update", "Update a Metrika counter.", {
        **_prop_counter(),
        "name": _prop_str("New name"),
        "site": _prop_str("New site domain"),
        "favorite": {"type": "boolean", "description": "Mark as favorite"},
    }, ["counter_id"]),
    _tool("yd_metrika_counter_delete", "Delete a Metrika counter.", {
        **_prop_counter(),
    }, ["counter_id"]),

    # ── GOALS (4) ────────────────────────────────────────────────────
    _tool("yd_metrika_goals_get", "List goals for a counter.", {
        **_prop_counter(),
    }, ["counter_id"]),
    _tool("yd_metrika_goal_create", "Create a goal for a counter.", {
        **_prop_counter(),
        "name": _prop_str("Goal name"),
        "goal_type": _prop_str("Goal type: url, number, step, action, phone, email, etc."),
        "conditions": {"type": "array", "items": {"type": "object"}, "description": "Conditions array, e.g. [{\"type\": \"contain\", \"url\": \"/thank-you\"}]"},
    }, ["counter_id", "name", "goal_type", "conditions"]),
    _tool("yd_metrika_goal_update", "Update a goal.", {
        **_prop_counter(),
        "goal_id": _prop_int("Goal ID"),
        "name": _prop_str("New goal name"),
        "goal_type": _prop_str("New goal type"),
        "conditions": {"type": "array", "items": {"type": "object"}, "description": "New conditions"},
    }, ["counter_id", "goal_id"]),
    _tool("yd_metrika_goal_delete", "Delete a goal.", {
        **_prop_counter(),
        "goal_id": _prop_int("Goal ID"),
    }, ["counter_id", "goal_id"]),

    # ── SEGMENTS (4) ─────────────────────────────────────────────────
    _tool("yd_metrika_segments_get", "List segments for a counter.", {
        **_prop_counter(),
    }, ["counter_id"]),
    _tool("yd_metrika_segment_create", "Create a segment.", {
        **_prop_counter(),
        "name": _prop_str("Segment name"),
        "expression": _prop_str("Segment expression"),
    }, ["counter_id", "name", "expression"]),
    _tool("yd_metrika_segment_update", "Update a segment.", {
        **_prop_counter(),
        "segment_id": _prop_int("Segment ID"),
        "name": _prop_str("New name"),
        "expression": _prop_str("New expression"),
    }, ["counter_id", "segment_id"]),
    _tool("yd_metrika_segment_delete", "Delete a segment.", {
        **_prop_counter(),
        "segment_id": _prop_int("Segment ID"),
    }, ["counter_id", "segment_id"]),

    # ── FILTERS (4) ──────────────────────────────────────────────────
    _tool("yd_metrika_filters_get", "List filters for a counter.", {
        **_prop_counter(),
    }, ["counter_id"]),
    _tool("yd_metrika_filter_create", "Create a filter for a counter.", {
        **_prop_counter(),
        "attr": _prop_str("Filter attribute (e.g. ip, title, url, referer)"),
        "type": _prop_str("Match type (equal, contain, start, etc.)"),
        "value": _prop_str("Filter value"),
        "action": _prop_str("Action: include or exclude"),
        "status": _prop_str("Status: active or disabled"),
    }, ["counter_id", "attr", "type", "value"]),
    _tool("yd_metrika_filter_update", "Update a filter.", {
        **_prop_counter(),
        "filter_id": _prop_int("Filter ID"),
        "attr": _prop_str("Filter attribute"),
        "type": _prop_str("Match type"),
        "value": _prop_str("Filter value"),
        "action": _prop_str("Action"),
        "status": _prop_str("Status"),
    }, ["counter_id", "filter_id"]),
    _tool("yd_metrika_filter_delete", "Delete a filter.", {
        **_prop_counter(),
        "filter_id": _prop_int("Filter ID"),
    }, ["counter_id", "filter_id"]),

    # ── GRANTS (4) ───────────────────────────────────────────────────
    _tool("yd_metrika_grants_get", "List access grants for a counter.", {
        **_prop_counter(),
    }, ["counter_id"]),
    _tool("yd_metrika_grant_add", "Add access grant to a counter.", {
        **_prop_counter(),
        "user_login": _prop_str("Yandex login of user"),
        "permission": _prop_str("Permission: view or edit"),
        "comment": _prop_str("Optional comment"),
    }, ["counter_id", "user_login"]),
    _tool("yd_metrika_grant_update", "Update access grant.", {
        **_prop_counter(),
        "user_login": _prop_str("Yandex login of user"),
        "permission": _prop_str("New permission: view or edit"),
    }, ["counter_id", "user_login", "permission"]),
    _tool("yd_metrika_grant_delete", "Delete access grant.", {
        **_prop_counter(),
        "user_login": _prop_str("Yandex login of user"),
    }, ["counter_id", "user_login"]),

    # ── REPORTS (4) ──────────────────────────────────────────────────
    _tool("yd_metrika_report", "Get a Metrika report (table).", {
        **_prop_counter(),
        "metrics": _prop_arr_str("Metrics, e.g. ym:s:visits, ym:s:pageviews, ym:s:bounceRate"),
        "dimensions": _prop_arr_str("Dimensions, e.g. ym:s:date, ym:s:searchEngine"),
        "date1": _prop_str("Start date YYYY-MM-DD (default: 30 days ago)"),
        "date2": _prop_str("End date YYYY-MM-DD (default: today)"),
        "filters": _prop_str("Filter expression"),
        "sort": _prop_arr_str("Sort fields"),
        "limit": _prop_int("Max rows (default 100)"),
    }, ["counter_id", "metrics"]),
    _tool("yd_metrika_report_by_time", "Get a Metrika report grouped by time.", {
        **_prop_counter(),
        "metrics": _prop_arr_str("Metrics"),
        "group": _prop_str("Grouping: day, week, month"),
        "dimensions": _prop_arr_str("Dimensions"),
        "date1": _prop_str("Start date YYYY-MM-DD"),
        "date2": _prop_str("End date YYYY-MM-DD"),
    }, ["counter_id", "metrics", "group"]),
    _tool("yd_metrika_report_comparison", "Get a Metrika comparison report (A vs B periods).", {
        **_prop_counter(),
        "metrics": _prop_arr_str("Metrics"),
        "date1_a": _prop_str("Period A start"),
        "date2_a": _prop_str("Period A end"),
        "date1_b": _prop_str("Period B start"),
        "date2_b": _prop_str("Period B end"),
        "dimensions": _prop_arr_str("Dimensions"),
    }, ["counter_id", "metrics", "date1_a", "date2_a", "date1_b", "date2_b"]),
    _tool("yd_metrika_report_drilldown", "Get a Metrika drilldown report.", {
        **_prop_counter(),
        "metrics": _prop_arr_str("Metrics"),
        "dimensions": _prop_arr_str("Dimensions"),
        "date1": _prop_str("Start date YYYY-MM-DD"),
        "date2": _prop_str("End date YYYY-MM-DD"),
        "parent_id": _prop_str("Parent row ID for drilldown"),
    }, ["counter_id", "metrics", "dimensions"]),

    # ── OFFLINE DATA (5) ─────────────────────────────────────────────
    _tool("yd_metrika_upload_conversions", "Upload offline conversions.", {
        **_prop_counter(),
        "conversions": {"type": "array", "items": {"type": "object"}, "description": "Array of conversions: [{DateTime, Target, ClientId?, Price?}]"},
        "client_id_type": _prop_str("CLIENT_ID or USER_ID (default CLIENT_ID)"),
    }, ["counter_id", "conversions"]),
    _tool("yd_metrika_conversions_status", "Get offline conversions upload status.", {
        **_prop_counter(),
    }, ["counter_id"]),
    _tool("yd_metrika_upload_calls", "Upload offline calls.", {
        **_prop_counter(),
        "calls": {"type": "array", "items": {"type": "object"}, "description": "Array of call data"},
        "client_id_type": _prop_str("CLIENT_ID or USER_ID (default CLIENT_ID)"),
    }, ["counter_id", "calls"]),
    _tool("yd_metrika_upload_expenses", "Upload advertising expenses.", {
        **_prop_counter(),
        "expenses": {"type": "array", "items": {"type": "object"}, "description": "Array of expense data"},
    }, ["counter_id", "expenses"]),
    _tool("yd_metrika_upload_user_params", "Upload user parameters.", {
        **_prop_counter(),
        "users": {"type": "array", "items": {"type": "object"}, "description": "Array of user data"},
        "client_id_type": _prop_str("CLIENT_ID or USER_ID (default CLIENT_ID)"),
    }, ["counter_id", "users"]),

    # ── LABELS (4) ───────────────────────────────────────────────────
    _tool("yd_metrika_labels_get", "List all labels."),
    _tool("yd_metrika_label_create", "Create a label.", {
        "name": _prop_str("Label name"),
    }, ["name"]),
    _tool("yd_metrika_label_update", "Update a label.", {
        "label_id": _prop_int("Label ID"),
        "name": _prop_str("New label name"),
    }, ["label_id", "name"]),
    _tool("yd_metrika_label_delete", "Delete a label.", {
        "label_id": _prop_int("Label ID"),
    }, ["label_id"]),

    # ── LABEL-COUNTER LINKS (2) ──────────────────────────────────────
    _tool("yd_metrika_label_link", "Link a counter to a label.", {
        **_prop_counter(),
        "label_id": _prop_int("Label ID"),
    }, ["counter_id", "label_id"]),
    _tool("yd_metrika_label_unlink", "Unlink a counter from a label.", {
        **_prop_counter(),
        "label_id": _prop_int("Label ID"),
    }, ["counter_id", "label_id"]),

    # ── ANNOTATIONS (4) ──────────────────────────────────────────────
    _tool("yd_metrika_annotations_get", "List chart annotations for a counter.", {
        **_prop_counter(),
    }, ["counter_id"]),
    _tool("yd_metrika_annotation_create", "Create a chart annotation.", {
        **_prop_counter(),
        "date": _prop_str("Date YYYY-MM-DD"),
        "title": _prop_str("Annotation title"),
        "message": _prop_str("Optional message"),
    }, ["counter_id", "date", "title"]),
    _tool("yd_metrika_annotation_update", "Update a chart annotation.", {
        **_prop_counter(),
        "annotation_id": _prop_int("Annotation ID"),
        "date": _prop_str("New date"),
        "title": _prop_str("New title"),
        "message": _prop_str("New message"),
    }, ["counter_id", "annotation_id"]),
    _tool("yd_metrika_annotation_delete", "Delete a chart annotation.", {
        **_prop_counter(),
        "annotation_id": _prop_int("Annotation ID"),
    }, ["counter_id", "annotation_id"]),

    # ── DELEGATES (3) ────────────────────────────────────────────────
    _tool("yd_metrika_delegates_get", "List all delegates."),
    _tool("yd_metrika_delegate_add", "Add a delegate.", {
        "user_login": _prop_str("Yandex login"),
        "comment": _prop_str("Optional comment"),
    }, ["user_login"]),
    _tool("yd_metrika_delegate_delete", "Delete a delegate.", {
        "user_login": _prop_str("Yandex login"),
    }, ["user_login"]),
]


# ── Handler registration ─────────────────────────────────────────────

def register_metrika_handlers(dispatch: dict):
    """Register all 43 Metrika tool handlers into dispatch dict."""

    # ── COUNTERS ─────────────────────────────────────────────────────

    async def counters_get(client, args, _token):
        params = {}
        if args.get("search_string"):
            params["search_string"] = args["search_string"]
        if args.get("favorite") is not None:
            params["favorite"] = "1" if args["favorite"] else "0"
        return await _crud_list(client, _token, "/management/v1/counters", params or None)

    async def counter_get(client, args, _token):
        return await _crud_list(client, _token, f"/management/v1/counter/{args['counter_id']}")

    async def counter_create(client, args, _token):
        body = {"counter": {"name": args["name"], "site2": {"site": args["site"]}}}
        return await _crud_create(client, _token, "/management/v1/counters", body)

    async def counter_update(client, args, _token):
        cid = args["counter_id"]
        counter = {}
        for k in ("name", "site", "favorite"):
            if args.get(k) is not None:
                counter[k] = args[k]
        return await _crud_update(client, _token, f"/management/v1/counter/{cid}", {"counter": counter})

    async def counter_delete(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/counter/{args['counter_id']}")

    # ── GOALS ────────────────────────────────────────────────────────

    async def goals_get(client, args, _token):
        return await _crud_list(client, _token, f"/management/v1/counter/{args['counter_id']}/goals")

    async def goal_create(client, args, _token):
        cid = args["counter_id"]
        body = {"goal": {
            "name": args["name"],
            "type": args["goal_type"],
            "conditions": args["conditions"],
        }}
        return await _crud_create(client, _token, f"/management/v1/counter/{cid}/goals", body)

    async def goal_update(client, args, _token):
        cid, gid = args["counter_id"], args["goal_id"]
        goal = {}
        for k in ("name", "goal_type", "conditions"):
            if args.get(k) is not None:
                goal["type" if k == "goal_type" else k] = args[k]
        return await _crud_update(client, _token, f"/management/v1/counter/{cid}/goal/{gid}", {"goal": goal})

    async def goal_delete(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/counter/{args['counter_id']}/goal/{args['goal_id']}")

    # ── SEGMENTS ─────────────────────────────────────────────────────

    async def segments_get(client, args, _token):
        return await _crud_list(client, _token, f"/management/v1/counter/{args['counter_id']}/apisegment/segments")

    async def segment_create(client, args, _token):
        cid = args["counter_id"]
        body = {"segment": {"name": args["name"], "expression": args["expression"]}}
        return await _crud_create(client, _token, f"/management/v1/counter/{cid}/apisegment/segments", body)

    async def segment_update(client, args, _token):
        cid, sid = args["counter_id"], args["segment_id"]
        seg = {}
        for k in ("name", "expression"):
            if args.get(k) is not None:
                seg[k] = args[k]
        return await _crud_update(client, _token, f"/management/v1/counter/{cid}/apisegment/segment/{sid}", {"segment": seg})

    async def segment_delete(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/counter/{args['counter_id']}/apisegment/segment/{args['segment_id']}")

    # ── FILTERS ──────────────────────────────────────────────────────

    async def filters_get(client, args, _token):
        return await _crud_list(client, _token, f"/management/v1/counter/{args['counter_id']}/filters")

    async def filter_create(client, args, _token):
        cid = args["counter_id"]
        filt = {"attr": args["attr"], "type": args["type"], "value": args["value"]}
        if args.get("action"):
            filt["action"] = args["action"]
        if args.get("status"):
            filt["status"] = args["status"]
        return await _crud_create(client, _token, f"/management/v1/counter/{cid}/filters", {"filter": filt})

    async def filter_update(client, args, _token):
        cid, fid = args["counter_id"], args["filter_id"]
        filt = {}
        for k in ("attr", "type", "value", "action", "status"):
            if args.get(k) is not None:
                filt[k] = args[k]
        return await _crud_update(client, _token, f"/management/v1/counter/{cid}/filter/{fid}", {"filter": filt})

    async def filter_delete(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/counter/{args['counter_id']}/filter/{args['filter_id']}")

    # ── GRANTS ───────────────────────────────────────────────────────

    async def grants_get(client, args, _token):
        return await _crud_list(client, _token, f"/management/v1/counter/{args['counter_id']}/grants")

    async def grant_add(client, args, _token):
        cid = args["counter_id"]
        grant = {"user_login": args["user_login"], "perm": args.get("permission", "view")}
        if args.get("comment"):
            grant["comment"] = args["comment"]
        return await _crud_create(client, _token, f"/management/v1/counter/{cid}/grants", {"grant": grant})

    async def grant_update(client, args, _token):
        cid, login = args["counter_id"], args["user_login"]
        body = {"grant": {"perm": args["permission"]}}
        return await _crud_update(client, _token, f"/management/v1/counter/{cid}/grant/{login}", body)

    async def grant_delete(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/counter/{args['counter_id']}/grant/{args['user_login']}")

    # ── REPORTS ──────────────────────────────────────────────────────

    async def report(client, args, _token):
        params = {
            "id": args["counter_id"],
            "metrics": ",".join(args["metrics"]),
        }
        if args.get("dimensions"):
            params["dimensions"] = ",".join(args["dimensions"])
        for k in ("date1", "date2", "filters"):
            if args.get(k):
                params[k] = args[k]
        if args.get("sort"):
            params["sort"] = ",".join(args["sort"])
        if args.get("limit"):
            params["limit"] = args["limit"]
        return await _crud_list(client, _token, "/stat/v1/data", params)

    async def report_by_time(client, args, _token):
        params = {
            "id": args["counter_id"],
            "metrics": ",".join(args["metrics"]),
            "group": args["group"],
        }
        if args.get("dimensions"):
            params["dimensions"] = ",".join(args["dimensions"])
        for k in ("date1", "date2"):
            if args.get(k):
                params[k] = args[k]
        return await _crud_list(client, _token, "/stat/v1/data/bytime", params)

    async def report_comparison(client, args, _token):
        params = {
            "id": args["counter_id"],
            "metrics": ",".join(args["metrics"]),
            "date1_a": args["date1_a"],
            "date2_a": args["date2_a"],
            "date1_b": args["date1_b"],
            "date2_b": args["date2_b"],
        }
        if args.get("dimensions"):
            params["dimensions"] = ",".join(args["dimensions"])
        return await _crud_list(client, _token, "/stat/v1/data/comparison", params)

    async def report_drilldown(client, args, _token):
        params = {
            "id": args["counter_id"],
            "metrics": ",".join(args["metrics"]),
            "dimensions": ",".join(args["dimensions"]),
        }
        for k in ("date1", "date2", "parent_id"):
            if args.get(k):
                params[k] = args[k]
        return await _crud_list(client, _token, "/stat/v1/data/drilldown", params)

    # ── OFFLINE DATA ─────────────────────────────────────────────────

    async def upload_conversions(client, args, _token):
        cid = args["counter_id"]
        ctype = args.get("client_id_type", "CLIENT_ID")
        path = f"/management/v1/counter/{cid}/offline_conversions/upload?client_id_type={ctype}"
        return await _crud_create(client, _token, path, {"conversions": args["conversions"]})

    async def conversions_status(client, args, _token):
        return await _crud_list(client, _token, f"/management/v1/counter/{args['counter_id']}/offline_conversions/uploadings")

    async def upload_calls(client, args, _token):
        cid = args["counter_id"]
        ctype = args.get("client_id_type", "CLIENT_ID")
        path = f"/management/v1/counter/{cid}/offline_conversions/calls/upload?client_id_type={ctype}"
        return await _crud_create(client, _token, path, {"calls": args["calls"]})

    async def upload_expenses(client, args, _token):
        cid = args["counter_id"]
        return await _crud_create(client, _token, f"/management/v1/counter/{cid}/expense/upload", {"expenses": args["expenses"]})

    async def upload_user_params(client, args, _token):
        cid = args["counter_id"]
        ctype = args.get("client_id_type", "CLIENT_ID")
        path = f"/management/v1/counter/{cid}/user_params/uploadings?client_id_type={ctype}"
        return await _crud_create(client, _token, path, {"users": args["users"]})

    # ── LABELS ───────────────────────────────────────────────────────

    async def labels_get(client, args, _token):
        return await _crud_list(client, _token, "/management/v1/labels")

    async def label_create(client, args, _token):
        return await _crud_create(client, _token, "/management/v1/labels", {"label": {"name": args["name"]}})

    async def label_update(client, args, _token):
        lid = args["label_id"]
        return await _crud_update(client, _token, f"/management/v1/label/{lid}", {"label": {"name": args["name"]}})

    async def label_delete(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/label/{args['label_id']}")

    # ── LABEL-COUNTER LINKS ──────────────────────────────────────────

    async def label_link(client, args, _token):
        return await _crud_create(client, _token, f"/management/v1/counter/{args['counter_id']}/label/{args['label_id']}", {})

    async def label_unlink(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/counter/{args['counter_id']}/label/{args['label_id']}")

    # ── ANNOTATIONS ──────────────────────────────────────────────────

    async def annotations_get(client, args, _token):
        return await _crud_list(client, _token, f"/management/v1/counter/{args['counter_id']}/chart_annotations")

    async def annotation_create(client, args, _token):
        cid = args["counter_id"]
        ann = {"date": args["date"], "title": args["title"]}
        if args.get("message"):
            ann["message"] = args["message"]
        return await _crud_create(client, _token, f"/management/v1/counter/{cid}/chart_annotation", {"annotation": ann})

    async def annotation_update(client, args, _token):
        cid, aid = args["counter_id"], args["annotation_id"]
        ann = {}
        for k in ("date", "title", "message"):
            if args.get(k) is not None:
                ann[k] = args[k]
        return await _crud_update(client, _token, f"/management/v1/counter/{cid}/chart_annotation/{aid}", {"annotation": ann})

    async def annotation_delete(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/counter/{args['counter_id']}/chart_annotation/{args['annotation_id']}")

    # ── DELEGATES ────────────────────────────────────────────────────

    async def delegates_get(client, args, _token):
        return await _crud_list(client, _token, "/management/v1/delegates")

    async def delegate_add(client, args, _token):
        d = {"user_login": args["user_login"]}
        if args.get("comment"):
            d["comment"] = args["comment"]
        return await _crud_create(client, _token, "/management/v1/delegates", {"delegate": d})

    async def delegate_delete(client, args, _token):
        return await _crud_delete(client, _token, f"/management/v1/delegate/{args['user_login']}")

    # ── Register all handlers ────────────────────────────────────────

    dispatch.update({
        # Counters
        "yd_metrika_counters_get": counters_get,
        "yd_metrika_counter_get": counter_get,
        "yd_metrika_counter_create": counter_create,
        "yd_metrika_counter_update": counter_update,
        "yd_metrika_counter_delete": counter_delete,
        # Goals
        "yd_metrika_goals_get": goals_get,
        "yd_metrika_goal_create": goal_create,
        "yd_metrika_goal_update": goal_update,
        "yd_metrika_goal_delete": goal_delete,
        # Segments
        "yd_metrika_segments_get": segments_get,
        "yd_metrika_segment_create": segment_create,
        "yd_metrika_segment_update": segment_update,
        "yd_metrika_segment_delete": segment_delete,
        # Filters
        "yd_metrika_filters_get": filters_get,
        "yd_metrika_filter_create": filter_create,
        "yd_metrika_filter_update": filter_update,
        "yd_metrika_filter_delete": filter_delete,
        # Grants
        "yd_metrika_grants_get": grants_get,
        "yd_metrika_grant_add": grant_add,
        "yd_metrika_grant_update": grant_update,
        "yd_metrika_grant_delete": grant_delete,
        # Reports
        "yd_metrika_report": report,
        "yd_metrika_report_by_time": report_by_time,
        "yd_metrika_report_comparison": report_comparison,
        "yd_metrika_report_drilldown": report_drilldown,
        # Offline data
        "yd_metrika_upload_conversions": upload_conversions,
        "yd_metrika_conversions_status": conversions_status,
        "yd_metrika_upload_calls": upload_calls,
        "yd_metrika_upload_expenses": upload_expenses,
        "yd_metrika_upload_user_params": upload_user_params,
        # Labels
        "yd_metrika_labels_get": labels_get,
        "yd_metrika_label_create": label_create,
        "yd_metrika_label_update": label_update,
        "yd_metrika_label_delete": label_delete,
        # Label-counter links
        "yd_metrika_label_link": label_link,
        "yd_metrika_label_unlink": label_unlink,
        # Annotations
        "yd_metrika_annotations_get": annotations_get,
        "yd_metrika_annotation_create": annotation_create,
        "yd_metrika_annotation_update": annotation_update,
        "yd_metrika_annotation_delete": annotation_delete,
        # Delegates
        "yd_metrika_delegates_get": delegates_get,
        "yd_metrika_delegate_add": delegate_add,
        "yd_metrika_delegate_delete": delegate_delete,
    })
