"""Explicit native gateway retry policy; unknown errors fail closed."""

def response_failure_kind(response):
    status = response.status_code
    if status >= 400:
        try:
            error = response.json().get("error", {})
            if isinstance(error, dict) and any(error.get(key) == "content_filter" for key in ("code", "type")):
                return "content_filter"
        except (ValueError, AttributeError):
            pass
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "auth_error"
    if status == 408:
        return "timeout"
    if 500 <= status <= 599:
        return "server_error"
    return "invalid_request"


def can_retry(policy, kind):
    return (policy.enabled and kind in {"timeout", "rate_limit", "server_error"}
            and kind in policy.retry_on and kind not in policy.do_not_retry_on)
