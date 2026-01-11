from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from zoneinfo import ZoneInfo

from .models import Priority


@dataclass(frozen=True)
class DueCheck:
    due_now: bool
    scheduled_dt: datetime


def due_for_daily_send(
    *,
    now: datetime,
    today: date,
    when_local: time,
    tz: ZoneInfo,
    last_sent_day: date | None,
    window_minutes: int = 10,
) -> DueCheck:
    """
    Returns due_now=True if:
    - not sent today
    - now is within [scheduled, scheduled+window]
    """
    scheduled = datetime.combine(today, when_local, tzinfo=tz)
    if last_sent_day == today:
        return DueCheck(due_now=False, scheduled_dt=scheduled)
    if now < scheduled:
        return DueCheck(due_now=False, scheduled_dt=scheduled)
    if now - scheduled > timedelta(minutes=window_minutes):
        return DueCheck(due_now=False, scheduled_dt=scheduled)
    return DueCheck(due_now=True, scheduled_dt=scheduled)


def carryover_priority(p: Priority) -> Priority:
    """
    When a task is not done today, we slightly increase its priority for tomorrow.
    """
    return {
        Priority.URGENT: Priority.URGENT,
        Priority.IMPORTANT: Priority.URGENT,
        Priority.OPTIONAL: Priority.IMPORTANT,
    }[p]

