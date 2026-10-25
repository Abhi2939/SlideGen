"""Extract only allowlisted quota metadata; never retain provider error text."""

from collections.abc import Mapping
import math
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

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


def duration_seconds(value):
    """Groq reset durations include compound values such as 1m2.5s."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{1,10}(?:\.\d{1,6})?", text):
        return float(text)
    if not re.fullmatch(r"(?:\d+(?:\.\d+)?(?:ms|s|m|h|d))+", text):
        return None
    units = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}
    seconds = sum(float(n) * units[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", text))
    return seconds if math.isfinite(seconds) and seconds <= 31536000 else None


def header_diagnostics(headers):
    diagnostics = {}
    if not isinstance(headers, Mapping):
        return diagnostics
    headers = {str(k).lower(): v for k, v in headers.items()}
    for header, name in _HEADERS.items():
        value = headers.get(header)
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
            diagnostics[name] = int(value)
    for header, name in (("x-ratelimit-reset-tokens", "tokens_reset_seconds"),
                         ("x-ratelimit-reset-requests", "requests_reset_seconds")):
        seconds = duration_seconds(headers.get(header))
        if seconds is not None:
            diagnostics[name] = seconds
    retry = headers.get("retry-after")
    seconds = duration_seconds(retry)
    if seconds is None and isinstance(retry, str):
        try:
            seconds = max(0, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    if seconds is not None:
        diagnostics["retry_after_seconds"] = math.ceil(seconds)
    return diagnostics


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
        retry = re.search(r"try again in ([0-9.hmsd]+)", message, re.I)
        if retry and (seconds := duration_seconds(retry.group(1))) is not None:
            diagnostics["retry_after_seconds"] = math.ceil(seconds)
    if isinstance(error, Mapping):
        kind = error.get("limit_type")
        if isinstance(kind, str) and kind in _METRICS.values():
            diagnostics["limit_type"] = kind
        seconds = duration_seconds(error.get("retry_after_seconds"))
        if seconds is None and isinstance(body, Mapping):
            seconds = duration_seconds(body.get("retry_after_seconds"))
        if seconds is not None:
            diagnostics["retry_after_seconds"] = math.ceil(seconds)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    diagnostics.update(header_diagnostics(headers))
    # Remaining RPD alone must never turn a TPM error into a daily error.
    kind = diagnostics.get("limit_type")
    if kind in {"requests_per_day", "tokens_per_day"}:
        # Groq's token reset header describes TPM, even on a TPD error.
        reset = diagnostics.get("requests_reset_seconds") if kind == "requests_per_day" else None
        reset = reset if reset is not None else diagnostics.get("retry_after_seconds")
        if reset is not None:
            diagnostics["reset_at"] = (datetime.now(timezone.utc) + timedelta(seconds=reset)).isoformat()
    return ProviderLimitError(diagnostics=diagnostics)


def safe_label(value: str) -> str:
    """Constrain log labels as strictly as provider error metadata."""
    return value if re.fullmatch(r"[a-zA-Z0-9._/-]{1,100}", value) else "unknown"
