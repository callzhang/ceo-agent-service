"""Deterministic IANA timezone conversion for calendar wall-clock times."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _offset_text(value: timedelta | None) -> str | None:
    if value is None:
        return None
    total_seconds = int(value.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_minutes = abs(total_seconds) // 60
    hours, minutes = divmod(total_minutes, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _conversion(
    local: datetime,
    source: ZoneInfo,
    target: ZoneInfo,
    fold: int,
) -> dict[str, str | int]:
    source_local = local.replace(tzinfo=source, fold=fold)
    utc = source_local.astimezone(UTC)
    target_local = utc.astimezone(target)
    return {
        "fold": fold,
        "source_local": source_local.isoformat(),
        "source_offset": _offset_text(source_local.utcoffset()) or "",
        "utc": utc.isoformat().replace("+00:00", "Z"),
        "target_local": target_local.isoformat(),
        "target_offset": _offset_text(target_local.utcoffset()) or "",
    }


def convert_calendar_timezone(
    local_time: str,
    source_timezone: str,
    target_timezone: str,
) -> dict[str, object]:
    """Convert an explicit local wall time between IANA timezones.

    Ambiguous and nonexistent source wall times are returned as such rather
    than silently choosing a DST fold or inventing a UTC instant.
    """
    try:
        local = datetime.fromisoformat(local_time)
    except (TypeError, ValueError):
        return {"status": "invalid_datetime", "local_time": local_time}
    if local.tzinfo is not None:
        return {
            "status": "offset_not_allowed",
            "local_time": local_time,
            "detail": "Pass a local wall time without UTC offset; provide its IANA timezone separately.",
        }
    try:
        source = ZoneInfo(source_timezone)
        target = ZoneInfo(target_timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return {
            "status": "invalid_timezone",
            "source_timezone": source_timezone,
            "target_timezone": target_timezone,
        }

    valid: dict[str, dict[str, str | int]] = {}
    for fold in (0, 1):
        candidate = _conversion(local, source, target, fold)
        roundtrip = datetime.fromisoformat(str(candidate["utc"]).replace("Z", "+00:00"))
        returned = roundtrip.astimezone(source)
        if returned.replace(tzinfo=None) == local and returned.fold == fold:
            valid[str(candidate["utc"])] = candidate

    common = {
        "source_timezone": source_timezone,
        "target_timezone": target_timezone,
        "local_time": local.isoformat(),
    }
    options = list(valid.values())
    if not options:
        return {"status": "nonexistent", **common, "options": []}
    if len(options) > 1:
        return {"status": "ambiguous", **common, "options": options}
    return {"status": "converted", **common, "conversion": options[0]}
