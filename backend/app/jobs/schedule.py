"""Report schedules (JasperReports Server: Schedule → Recurrence / Parameters / Output / Notifications).

Pure functions only: checking a schedule sent by the UI, the next run time, and relative date values such as
{today-1} or {prevMonthStart} that are resolved on the day the job runs."""
from __future__ import annotations

import calendar
import os
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.app.constants import OUTPUT_FORMATS, REPORT_ID_PATTERN

RECURRENCE_TYPES = {"once", "minutes", "daily", "weekly", "monthly"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
EMAIL_RE = re.compile(r"^[^@\s,;<>\"]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
RELATIVE_RE = re.compile(r"^\{\s*(today|yesterday|weekStart|monthStart|monthEnd|prevMonthStart|prevMonthEnd|yearStart|prevYearStart|prevYearEnd)"
                         r"\s*(?:([+-])\s*(\d{1,4}))?\s*\}$")
MAX_RECIPIENTS = 20
MIN_INTERVAL_MINUTES = 5


def default_timezone() -> str:
    return os.getenv("REPORTING_TIMEZONE", "").strip() or "UTC"


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown time zone: {name!r} (use a name like Asia/Karachi or UTC)") from None


def new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"


# ----------------------------------------------------------------- relative dates
def resolve_relative(value: Any, today: date) -> Any:
    """{today}, {today-1}, {yesterday}, {weekStart}, {monthStart}, {monthEnd}, {prevMonthStart}, {prevMonthEnd},
    {yearStart}, {prevYearStart}, {prevYearEnd}; an optional +N / -N adds days. Other values are returned unchanged."""
    if not isinstance(value, str):
        return value
    found = RELATIVE_RE.fullmatch(value.strip())
    if not found:
        return value
    name, sign, amount = found.groups()
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    base = {
        "today": today,
        "yesterday": today - timedelta(days=1),
        "weekStart": today - timedelta(days=today.weekday()),
        "monthStart": month_start,
        "monthEnd": today.replace(day=calendar.monthrange(today.year, today.month)[1]),
        "prevMonthStart": prev_month_end.replace(day=1),
        "prevMonthEnd": prev_month_end,
        "yearStart": date(today.year, 1, 1),
        "prevYearStart": date(today.year - 1, 1, 1),
        "prevYearEnd": date(today.year - 1, 12, 31),
    }[name]
    if amount:
        base += timedelta(days=int(amount) * (-1 if sign == "-" else 1))
    return base.isoformat()


# ----------------------------------------------------------------- recurrence
def clean_recurrence(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("recurrence must be a JSON object")
    kind = str(raw.get("type", ""))
    if kind not in RECURRENCE_TYPES:
        raise ValueError(f"recurrence.type must be one of {', '.join(sorted(RECURRENCE_TYPES))}")
    if kind == "once":
        try:
            at = datetime.fromisoformat(str(raw.get("at", "")))
        except ValueError:
            raise ValueError("recurrence.at must be a date and time like 2026-12-01T08:00") from None
        return {"type": kind, "at": at.replace(tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes")}
    if kind == "minutes":
        every = raw.get("every")
        if not isinstance(every, int) or isinstance(every, bool) or not MIN_INTERVAL_MINUTES <= every <= 1440:
            raise ValueError(f"recurrence.every must be {MIN_INTERVAL_MINUTES} to 1440 minutes")
        return {"type": kind, "every": every}
    clock = str(raw.get("time", ""))
    if not TIME_RE.fullmatch(clock):
        raise ValueError("recurrence.time must be HH:MM (24 hours)")
    out: dict[str, Any] = {"type": kind, "time": clock}
    if kind == "weekly":
        days = raw.get("weekdays")
        if not isinstance(days, list) or not days or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days):
            raise ValueError("recurrence.weekdays must list days 0 (Monday) to 6 (Sunday)")
        out["weekdays"] = sorted(set(days))
    if kind == "monthly":
        day = raw.get("day")
        if day != "last" and (not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31):
            raise ValueError('recurrence.day must be 1 to 31 or "last"')
        out["day"] = day
    return out


def next_run(recurrence: dict[str, Any], tz_name: str, after: datetime) -> datetime | None:
    """The first run time strictly after `after` (UTC), as UTC; None when a one-time job is over."""
    tz = zone(tz_name)
    after = after.astimezone(timezone.utc)
    local_after = after.astimezone(tz)
    kind = recurrence["type"]

    def at(day: date, clock: str) -> datetime:
        hours, minutes = (int(x) for x in clock.split(":"))
        return datetime.combine(day, time(hours, minutes), tzinfo=tz).astimezone(timezone.utc)

    if kind == "once":
        moment = datetime.fromisoformat(recurrence["at"]).replace(tzinfo=tz).astimezone(timezone.utc)
        return moment if moment > after else None
    if kind == "minutes":
        start = after.replace(second=0, microsecond=0)
        return start + timedelta(minutes=recurrence["every"])
    if kind == "daily":
        for offset in range(3):
            moment = at(local_after.date() + timedelta(days=offset), recurrence["time"])
            if moment > after:
                return moment
    if kind == "weekly":
        for offset in range(9):
            day = local_after.date() + timedelta(days=offset)
            if day.weekday() in recurrence["weekdays"]:
                moment = at(day, recurrence["time"])
                if moment > after:
                    return moment
    if kind == "monthly":
        year, month = local_after.year, local_after.month
        for _ in range(14):
            last = calendar.monthrange(year, month)[1]
            day = last if recurrence["day"] == "last" else min(int(recurrence["day"]), last)
            moment = at(date(year, month, day), recurrence["time"])
            if moment > after:
                return moment
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


def describe(recurrence: dict[str, Any]) -> str:
    kind = recurrence["type"]
    if kind == "once":
        return f"Once at {recurrence['at'].replace('T', ' ')}"
    if kind == "minutes":
        return f"Every {recurrence['every']} minutes"
    if kind == "daily":
        return f"Every day at {recurrence['time']}"
    if kind == "weekly":
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return f"Every {', '.join(names[d] for d in recurrence['weekdays'])} at {recurrence['time']}"
    day = "the last day" if recurrence["day"] == "last" else f"day {recurrence['day']}"
    return f"Monthly on {day} at {recurrence['time']}"


# ----------------------------------------------------------------- whole job
def clean_job(raw: Any, *, report_ids: set[str], allow_email: bool) -> dict[str, Any]:
    """Check a schedule sent by the UI. Owner, ids and run state are set by the caller."""
    if not isinstance(raw, dict):
        raise ValueError("The schedule must be a JSON object")
    report_id = str(raw.get("reportId", ""))
    if not REPORT_ID_PATTERN.fullmatch(report_id) or report_id not in report_ids:
        raise ValueError("Choose a report that is published on the server")
    output_format = str(raw.get("format", "pdf")).lower()
    if output_format not in OUTPUT_FORMATS:
        raise ValueError("format must be pdf, xlsx or csv")
    tz_name = str(raw.get("timezone") or default_timezone())
    zone(tz_name)
    parameters = raw.get("parameters") or {}
    if not isinstance(parameters, dict) or len(parameters) > 50:
        raise ValueError("parameters must be a JSON object")
    clean_parameters = {}
    for key, value in parameters.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", str(key)):
            raise ValueError(f"Invalid parameter name: {key!r}")
        if value is None or value == "":
            continue
        if isinstance(value, list):
            value = ",".join(str(v) for v in value)
        clean_parameters[str(key)] = str(value)[:2000]
    email = raw.get("email") or {}
    if not isinstance(email, dict):
        raise ValueError("email must be a JSON object")
    recipients = email.get("to") or []
    if isinstance(recipients, str):
        recipients = [part.strip() for part in re.split(r"[,;\s]+", recipients) if part.strip()]
    if not isinstance(recipients, list) or len(recipients) > MAX_RECIPIENTS:
        raise ValueError(f"Give at most {MAX_RECIPIENTS} e-mail recipients")
    bad = [r for r in recipients if not isinstance(r, str) or not EMAIL_RE.fullmatch(r)]
    if bad:
        raise ValueError(f"Not an e-mail address: {str(bad[0])[:80]}")
    if recipients and not allow_email:
        raise ValueError("Only designers and administrators can send scheduled reports by e-mail")
    keep = raw.get("keep", 10)
    if not isinstance(keep, int) or isinstance(keep, bool) or not 1 <= keep <= 100:
        raise ValueError("keep must be 1 to 100 output files")
    label = str(raw.get("label", "") or "").strip()[:120]
    return {
        "label": label or report_id,
        "reportId": report_id,
        "format": output_format,
        "parameters": clean_parameters,
        "recurrence": clean_recurrence(raw.get("recurrence")),
        "timezone": tz_name,
        "enabled": raw.get("enabled", True) is not False,
        "keep": keep,
        "email": {
            "to": list(dict.fromkeys(recipients)),
            "subject": str(email.get("subject", "") or "")[:200].replace("\r", " ").replace("\n", " "),
            "message": str(email.get("message", "") or "")[:4000],
            "attach": email.get("attach", True) is not False,
        },
    }
