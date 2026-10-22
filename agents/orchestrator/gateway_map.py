"""Gateway map: how APIM is configured for this app, read live from Azure Resource Manager.

Returns the global policy and one lane per API (model, MCP, A2A). Each policy is turned into an
ordered list of steps: what kind of step it is, a one-line summary with the current named values,
its effect for each caller, the knobs it uses and its line range in the policy XML.

APIM's raw policy text is not strict XML (expressions hold unescaped quotes and generics), so the
structure comes from the escaped "xml" format and the line ranges are located in the raw text,
which is what the UI shows. The element order is the same in both.
"""

import asyncio
import re
import xml.etree.ElementTree as ET

import httpx

import knobs

API_VERSION = "2025-09-01-preview"
UNKNOWN = "unknown"
_KNOB = re.compile(r"\{\{([\w-]+)\}\}")


async def _get(http: httpx.AsyncClient, path: str, **params) -> httpx.Response:
    response = await http.get(f"{knobs.ARM}{knobs.APIM_RESOURCE_ID}{path}", params={"api-version": API_VERSION, **params})
    response.raise_for_status()
    return response


async def _policy(http: httpx.AsyncClient, path: str) -> tuple[str, str] | None:
    """(escaped xml, raw text) of a policy, or None when the scope has no policy."""
    try:
        xml, raw = await asyncio.gather(_get(http, path, format="xml"), _get(http, path, format="rawxml"))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return xml.content.decode("utf-8-sig"), raw.content.decode("utf-8-sig")


# ---------- locating elements in the raw text ----------


def _locate(root: ET.Element, raw: str) -> dict[int, tuple[int, int]]:
    """Line range (1-based, inclusive) of every element, found by walking both documents in order."""
    starts = [0] + [m.end() for m in re.finditer("\n", raw)]

    def line(offset: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    ranges: dict[int, tuple[int, int]] = {}
    cursor = raw.find("<policies")
    for element in root.iter():
        match = re.compile(rf"<{re.escape(element.tag)}[\s/>]").search(raw, max(cursor, 0))
        if match is None:
            continue
        start = match.start()
        cursor = start + 1
        if len(element) == 0 and not (element.text or "").strip():
            end = raw.find("/>", start)
            end = end + 2 if end >= 0 else start
        else:
            depth, pos, end = 0, start, len(raw)
            tokens = re.compile(rf"<{re.escape(element.tag)}[\s>]|</{re.escape(element.tag)}>")
            while (token := tokens.search(raw, pos)) is not None:
                depth += -1 if token.group().startswith("</") else 1
                pos = token.end()
                if depth == 0:
                    end = pos
                    break
        ranges[id(element)] = (line(start), line(end))
    return ranges


# ---------- reading policies into steps ----------


def _knobs_in(element: ET.Element) -> list[str]:
    found = _KNOB.findall(ET.tostring(element, encoding="unicode"))
    return list(dict.fromkeys(found))


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip() and v.strip() != "none"]


def _status(element: ET.Element) -> int | None:
    status = element.find(".//return-response/set-status")
    return int(status.get("code")) if status is not None and status.get("code", "").isdigit() else None


def _fmt(number: str | None) -> str:
    return f"{int(number):,}" if number and number.isdigit() else (number or "?")


class _Reader:
    def __init__(self, values: dict[str, str | None], callers: list[str]):
        self.values = values
        self.callers = callers  # known agent names; UNKNOWN is added for every caller-aware step

    def value(self, knob: str) -> str | None:
        return self.values.get(knob)

    def steps(self, root: ET.Element, raw: str) -> list[dict]:
        ranges = _locate(root, raw)
        steps: list[dict] = []
        for section in ("inbound", "backend", "outbound", "on-error"):
            node = root.find(section)
            if node is None:
                continue
            for element in node:
                if element.tag == "base" and section != "inbound":
                    continue  # the global policy's own sections are shown on its own row
                step = self.step(element) | {"section": section, "lines": list(ranges.get(id(element), (0, 0)))}
                step.setdefault("knobs", _knobs_in(element))
                previous = steps[-1] if steps else None
                # Consecutive response headers read as one step.
                if previous and previous["kind"] == step["kind"] == "stamp-headers" and previous["section"] == section:
                    previous["lines"][1] = step["lines"][1]
                    previous["summary"] += ", " + step["summary"]
                    continue
                steps.append(step)
        for index, step in enumerate(steps):
            step["id"] = index
        return steps

    def everyone(self, state: str, label: str = "") -> dict:
        return {caller: {"state": state, "label": label} for caller in [*self.callers, UNKNOWN]}

    def step(self, el: ET.Element) -> dict:
        tag = el.tag
        if tag == "base":
            return {"kind": "base", "title": "Global policy", "summary": "Identity checks run first", "minor": True}
        if tag == "validate-azure-ad-token":
            apps = [_KNOB.sub(r"\1", a.text or "") for a in el.iter("application-id")]
            audiences = [a.text for a in el.iter("audience")]
            effect = self.everyone("pass")
            effect[UNKNOWN] = {"state": "deny", "label": el.get("failed-validation-httpcode", "401")}
            return {
                "kind": "validate-token",
                "title": "Validate Entra token",
                "summary": f"{len(apps)} approved identities · audience {', '.join(audiences)}",
                "effect": effect,
            }
        if tag == "set-variable":
            name = el.get("name")
            if name == "callerName":
                return {"kind": "identify-caller", "title": "Identify the caller", "summary": "Client id → agent name", "effect": self.everyone("pass")}
            if name == "mcpTool":
                return {"kind": "read-tool", "title": "Read tool name", "summary": "From the JSON-RPC body", "minor": True}
            return {"kind": "variable", "title": f"Set {name}", "summary": "Read from the token claims" if name == "callerAppId" else "", "minor": True}
        if tag == "trace":
            return {"kind": "trace", "title": "Trace", "summary": "Caller into the gateway logs", "minor": True}
        if tag == "choose":
            return self.choose(el)
        if tag == "llm-token-limit":
            tpm = self.value(_KNOB.sub(r"\1", el.get("tokens-per-minute", "")))
            return {"kind": "token-limit", "title": "Token budget", "summary": f"{_fmt(tpm)} tokens/min", "effect": self.everyone("limit", f"{_fmt(tpm)} TPM")}
        if tag == "rate-limit-by-key":
            knob = _KNOB.sub(r"\1", el.get("calls", ""))
            calls, period = self.value(knob) or el.get("calls"), el.get("renewal-period", "60")
            label = f"{_fmt(calls)} req / {period}s"
            return {"kind": "rate-limit", "title": "Request rate limit", "summary": f"{label} per agent", "effect": self.everyone("limit", label)}
        if tag == "llm-emit-token-metric":
            dims = [d.get("name") for d in el.iter("dimension")]
            return {"kind": "token-metric", "title": "Token metrics", "summary": "Per " + ", ".join(dims), "minor": True}
        if tag == "set-backend-service":
            return {"kind": "route", "title": "Route to backend", "summary": el.get("backend-id") or el.get("base-url", ""), "minor": True}
        if tag == "authentication-managed-identity":
            return {"kind": "apim-identity", "title": "APIM signs in", "summary": "With its own managed identity"}
        if tag == "set-header":
            return self.header(el)
        if tag == "forward-request":
            return {"kind": "forward", "title": "Forward", "summary": "To the API's backend", "minor": True}
        return {"kind": "other", "title": tag.replace("-", " ").capitalize(), "summary": "", "minor": True}

    def header(self, el: ET.Element) -> dict:
        name, action = el.get("name", ""), el.get("exists-action", "override")
        value = "".join(el.itertext()).strip()
        if name.lower() == "authorization" and action == "delete":
            return {"kind": "strip-token", "title": "Strip agent token", "summary": "Backend never sees it"}
        if name.lower() == "authorization":
            return {"kind": "swap-token", "title": "Use APIM's token", "summary": "Bearer token from APIM's identity", "minor": True}
        if _KNOB.search(value):
            return {"kind": "gateway-key", "title": "Add gateway key", "summary": f"{name} · secret named value"}
        if name.lower().startswith("x-apim-"):
            return {"kind": "stamp-headers", "title": "Stamp response headers", "summary": name, "minor": True}
        return {"kind": "header", "title": f"Set {name}", "summary": "Caller name for the backend" if "agent" in name.lower() else "", "minor": True}

    def choose(self, el: ET.Element) -> dict:
        whens = el.findall("when")
        otherwise = el.find("otherwise")
        # Per-agent token budgets: one branch per agent, everyone else refused.
        if any(w.find(".//llm-token-limit") is not None for w in whens):
            effect: dict = {}
            budgets = []
            for w in whens:
                agent = re.search(r'==\s*"([\w-]+)"', w.get("condition", ""))
                limit = w.find(".//llm-token-limit")
                if agent and limit is not None:
                    tpm = self.value(_KNOB.sub(r"\1", limit.get("tokens-per-minute", "")))
                    effect[agent.group(1)] = {"state": "limit", "label": f"{_fmt(tpm)} TPM"}
                    budgets.append(f"{agent.group(1).removesuffix('-agent')} {_fmt(tpm)}")
            refused = _status(otherwise) if otherwise is not None else None
            for caller in [*self.callers, UNKNOWN]:
                effect.setdefault(caller, {"state": "deny", "label": str(refused or 403)})
            return {"kind": "token-limit", "title": "Token budget per agent", "summary": " · ".join(budgets) + " tokens/min", "effect": effect}
        if len(whens) == 1 and otherwise is None:
            when, status = whens[0], _status(whens[0])
            used = _KNOB.findall(when.get("condition", ""))
            # Allow-list gate: the condition checks the caller against a list knob.
            if status in (401, 403) and used and "callerName" in when.get("condition", ""):
                allowed = _list(self.value(used[0]))
                effect = {c: {"state": "pass" if c in allowed else "deny", "label": "" if c in allowed else str(status)} for c in self.callers}
                effect[UNKNOWN] = {"state": "deny", "label": str(status)}
                who = ", ".join(a.removesuffix("-agent") for a in allowed) or "nobody"
                return {"kind": "caller-gate", "title": "Allowed callers", "summary": f"Only {who}", "knobs": used, "effect": effect}
            # Tool block: answered with an MCP tool error (200) instead of an HTTP error.
            if status == 200 and used:
                blocked = _list(self.value(used[0]))
                summary = "Blocked: " + ", ".join(blocked) if blocked else "No tools blocked"
                state = "block" if blocked else "pass"
                return {"kind": "tool-block", "title": "Blocked tools", "summary": summary, "knobs": used, "blocked": blocked, "effect": self.everyone(state, f"{len(blocked)} blocked" if blocked else "")}
        return {"kind": "condition", "title": "Condition", "summary": f"{len(whens)} branch(es)", "minor": True}


def _callers(root: ET.Element) -> list[dict]:
    """Agents the global policy knows, from the client id → name mapping in callerName."""
    for el in root.iter("set-variable"):
        if el.get("name") == "callerName":
            pairs = re.findall(r'\{\{([\w-]+)\}\}"\s*\)\s*\{\s*return\s*"([\w-]+)"', el.get("value", ""))
            return [{"name": name, "clientIdKnob": knob} for knob, name in pairs]
    return []


def _lane_kind(api: dict) -> str:
    kind = api["properties"].get("type")
    return kind if kind in ("mcp", "a2a") else "model"


async def build() -> dict:
    async with httpx.AsyncClient(timeout=30, headers=await knobs._headers()) as http:
        global_policy, apis, backends, named = await asyncio.gather(
            _policy(http, "/policies/policy"), _get(http, "/apis"), _get(http, "/backends"), _get(http, "/namedValues")
        )
        api_list = [a for a in apis.json()["value"] if a["properties"].get("isCurrent", True)]
        policies = await asyncio.gather(*(_policy(http, f"/apis/{a['name']}/policies/policy") for a in api_list))

    values: dict[str, str | None] = {}
    secrets: list[str] = []
    for nv in named.json()["value"]:
        props = nv["properties"]
        values[nv["name"]] = None if props.get("secret") else props.get("value")
        if props.get("secret"):
            secrets.append(nv["name"])
    backend_urls = {b["name"]: b["properties"].get("url") for b in backends.json()["value"]}

    global_root = ET.fromstring(global_policy[0]) if global_policy else ET.Element("policies")
    callers = _callers(global_root)
    reader = _Reader(values, [c["name"] for c in callers])
    for caller in callers:
        caller["clientId"] = values.get(caller["clientIdKnob"])

    lanes = []
    for api, policy in zip(api_list, policies, strict=True):
        props = api["properties"]
        root = ET.fromstring(policy[0]) if policy else ET.Element("policies")
        backend_id = next((el.get("backend-id") for el in root.iter("set-backend-service")), None) or props.get("backendId")
        json_rpc = props.get("jsonRpcProperties") or {}
        url = backend_urls.get(backend_id) or props.get("serviceUrl") or json_rpc.get("BackendUrl") or json_rpc.get("backendUrl")
        lanes.append(
            {
                "id": api["name"],
                "name": props.get("displayName", api["name"]),
                "kind": _lane_kind(api),
                "path": "/" + props.get("path", ""),
                "backend": {"id": backend_id, "url": url},
                "steps": reader.steps(root, policy[1]) if policy else [],
                "xml": policy[1] if policy else "",
            }
        )
    lanes.sort(key=lambda lane: ["model", "mcp", "a2a"].index(lane["kind"]))

    # Only the named values the policies use (APIM keeps internal ones, e.g. logger keys, too).
    used = set(_KNOB.findall(global_policy[1] if global_policy else "")) | {k for lane in lanes for k in _KNOB.findall(lane["xml"])}

    return {
        "service": knobs.APIM_RESOURCE_ID.rsplit("/", 1)[-1],
        "callers": [*callers, {"name": UNKNOWN, "clientIdKnob": None, "clientId": None}],
        "global": {"steps": reader.steps(global_root, global_policy[1]) if global_policy else [], "xml": global_policy[1] if global_policy else ""},
        "lanes": lanes,
        "namedValues": {k: v for k, v in values.items() if k in used},
        "secrets": [k for k in secrets if k in used],
        "knobs": [k.name for k in knobs.KNOBS],
    }
