"""Turn gateway failures into plain explanations for the model and the UI.

Client libraries bury APIM's answer: the MCP client raises it from a task group, the A2A client
wraps it in its own error. ``gateway_error`` digs out the HTTP status and the reason APIM gave.
"""

import json

import httpx


def _find_status_error(exc: BaseException) -> httpx.HTTPStatusError | None:
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            return current
        stack.extend(getattr(current, "exceptions", ()))  # ExceptionGroup
        stack.extend((current.__cause__, current.__context__))
    return None


def response_reason(response: httpx.Response) -> str:
    """The error message in an APIM response body (our policies use "error", APIM itself "message")."""
    try:
        body = response.text
    except httpx.ResponseNotRead:
        return ""
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:300]
    if isinstance(data, dict):
        return str(data.get("error") or data.get("message") or "")
    return ""


def gateway_error(exc: BaseException) -> tuple[int | None, str]:
    """(HTTP status, explanation) for a failed call through APIM; status is None if none was found."""
    status_error = _find_status_error(exc)
    if status_error is None:
        return None, str(exc)
    status = status_error.response.status_code
    reason = response_reason(status_error.response)
    if status == 429:
        text = "API Management throttled this agent (token or request limit reached); retrying within a minute will fail too"
    elif status in (401, 403):
        text = f"API Management policy denied access (HTTP {status}); this is a policy decision, retrying will not help"
    else:
        text = f"API Management returned HTTP {status}"
    return status, f"{text}. {reason}".strip() if reason else text + "."
