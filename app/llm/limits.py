"""Extract only allowlisted quota metadata; never retain provider error text."""

from collections.abc import Mapping
import math
import re

from app.core.errors import ProviderLimitError


_METRICS = {
    "tokens per minute (TPM)": "tokens_per_minute",
    "tokens per day (TPD)": "tokens_per_day",
    "requests per minute (RPM)": "requests_per_minute",
    "requests per day (RPD)": "requests_per_day",
    "input tokens per minute (ITPM)": "input_tokens_per_minute",
    "output tokens per minute (OTPM)": "output_tokens_per_minute",
}
_HEADERS = {
    "x-ratelimit-limit-requests": "requests_per_day_limit",
    "x-ratelimit-remaining-requests": "requests_per_day_remaining",
    "x-ratelimit-limit-tokens": "tokens_per_minute_limit",
    "x-ratelimit-remaining-tokens": "tokens_per_minute_remaining",
}


def provider_limit_error(exc: Exception) -> ProviderLimitError:
    diagnostics = {}
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, Mapping) else None
    message = error.get("message") if isinstance(error, Mapping) else None
    if isinstance(message, str):
        for phrase, metric in _METRICS.items():
            if f"on {phrase}:" in message:
                diagnostics["limit_type"] = metric
                break
        # Match the provider's numeric summary, never copy surrounding text.
        counts = re.search(r"\bLimit (\d{1,12}), Used (\d{1,12}), Requested (\d{1,12})\b", message)
        if counts:
            diagnostics.update(zip(("limit", "used", "requested"), map(int, counts.groups())))
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    if isinstance(headers, Mapping):
        for header, name in _HEADERS.items():
            value = headers.get(header)
            if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
                diagnostics[name] = int(value)
        retry = headers.get("retry-after")
        if isinstance(retry, str) and re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,6})?", retry):
            diagnostics["retry_after_seconds"] = math.ceil(float(retry))
    return ProviderLimitError(diagnostics=diagnostics)


def safe_label(value: str) -> str:
    """Constrain log labels as strictly as provider error metadata."""
    return value if re.fullmatch(r"[a-zA-Z0-9._/-]{1,100}", value) else "unknown"
