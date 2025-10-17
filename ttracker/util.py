import datetime as dt
import re
from typing import Tuple

# The "day" boundary is at 06:00 local time
DAY_CUTOFF_HOUR = 6


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def day_start(ts: dt.datetime) -> dt.datetime:
    local = ts.astimezone()
    base = local.replace(hour=DAY_CUTOFF_HOUR, minute=0, second=0, microsecond=0)
    if local.hour < DAY_CUTOFF_HOUR:
        base = base - dt.timedelta(days=1)
    return base


def day_end(ts: dt.datetime) -> dt.datetime:
    return day_start(ts) + dt.timedelta(days=1)


def week_range(ts: dt.datetime) -> Tuple[dt.datetime, dt.datetime]:
    # Week Monday..Sunday using shifted days at 06:00 boundary
    start = day_start(ts)
    # weekday: Monday=0
    monday = start - dt.timedelta(days=start.weekday())
    return monday, monday + dt.timedelta(days=7)


def month_range(ts: dt.datetime) -> Tuple[dt.datetime, dt.datetime]:
    # Month with cut at 06:00 for each day
    start = day_start(ts)
    first_day = start.replace(day=1)
    if first_day.hour != DAY_CUTOFF_HOUR:
        first_day = first_day.replace(hour=DAY_CUTOFF_HOUR, minute=0, second=0, microsecond=0)
    # next month
    if first_day.month == 12:
        next_month = first_day.replace(year=first_day.year + 1, month=1)
    else:
        next_month = first_day.replace(month=first_day.month + 1)
    return first_day, next_month


def humanize_seconds(sec: int) -> str:
    neg = sec < 0
    sec = abs(int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        body = f"{h}h {m}m"
    elif m > 0:
        body = f"{m}m {s}s"
    else:
        body = f"{s}s"
    return f"-{body}" if neg else body


_DURATION_RE = re.compile(r"^\s*([+-]?)\s*(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+(?:\.\d+)?)\s*m)?\s*(?:(\d+(?:\.\d+)?)\s*s)?\s*$",
                          re.IGNORECASE)


def parse_duration_delta(s: str) -> int:
    """
    Parse strings like: +5h, -30m, -1.5h, +2h15m, 90m, 360s, 1h 20m 10s
    Returns signed seconds as int.
    Raises ValueError on invalid input.
    """
    m = _DURATION_RE.match(s)
    if not m:
        raise ValueError("Invalid duration format")
    sign_str, h_s, m_s, s_s = m.groups()
    total = 0.0
    if h_s:
        total += float(h_s) * 3600
    if m_s:
        total += float(m_s) * 60
    if s_s:
        total += float(s_s)
    if not (h_s or m_s or s_s):
        raise ValueError("Specify at least one of hours/minutes/seconds")
    if sign_str == '-':
        total = -total
    return int(round(total))


def split_by_day_boundary(start: dt.datetime, end: dt.datetime) -> Tuple[Tuple[dt.datetime, dt.datetime], ...]:
    """
    Split interval [start, end) by 06:00 day boundaries.
    Returns tuple of (s, e) fragments.
    """
    if end <= start:
        return ()
    frags = []
    cur = start
    while True:
        ds = day_start(cur)
        de = ds + dt.timedelta(days=1)
        segment_end = min(de, end)
        frags.append((cur, segment_end))
        if segment_end >= end:
            break
        cur = segment_end
    return tuple(frags)
