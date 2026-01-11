from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Sequence

from zoneinfo import ZoneInfo

from .models import Priority, ScheduledBlock


@dataclass(frozen=True)
class PlanWindow:
    start_local: time = time(9, 0)
    end_local: time = time(18, 0)
    break_minutes: int = 5


@dataclass(frozen=True)
class PlanTask:
    task_id: int
    title: str
    estimated_minutes: int
    due_local_time: Optional[time]
    priority: Priority


def build_schedule(
    *,
    day: date,
    tz: ZoneInfo,
    tasks: Sequence[PlanTask],
    window: PlanWindow = PlanWindow(),
) -> list[ScheduledBlock]:
    """
    Simple greedy planner:
    - Sort by (priority, due_time, shorter first as tiebreaker)
    - Place tasks sequentially within window
    - If due_time exists and we would end after it, we still place it but it may violate the due
      (we keep it early because urgent/due tasks are sorted first).
    """
    start_dt = datetime.combine(day, window.start_local, tzinfo=tz)
    end_dt = datetime.combine(day, window.end_local, tzinfo=tz)

    def prio_rank(p: Priority) -> int:
        return {Priority.URGENT: 0, Priority.IMPORTANT: 1, Priority.OPTIONAL: 2}[p]

    def due_rank(t: PlanTask) -> tuple[int, int]:
        if t.due_local_time is None:
            return (1, 0)
        return (0, t.due_local_time.hour * 60 + t.due_local_time.minute)

    ordered = sorted(
        tasks,
        key=lambda t: (prio_rank(t.priority), due_rank(t), t.estimated_minutes, t.task_id),
    )

    cur = start_dt
    blocks: list[ScheduledBlock] = []
    for t in ordered:
        dur = timedelta(minutes=max(1, int(t.estimated_minutes)))
        if cur + dur > end_dt:
            break
        block = ScheduledBlock(
            task_id=t.task_id,
            title=t.title,
            start_local=cur,
            end_local=cur + dur,
            priority=t.priority,
        )
        blocks.append(block)
        cur = block.end_local + timedelta(minutes=max(0, int(window.break_minutes)))
        if cur >= end_dt:
            break
    return blocks


def next_occurrence_local(day: date, when_local: time, tz: ZoneInfo) -> datetime:
    """Helper for jobs: build timezone-aware datetime for the given day+time."""
    return datetime.combine(day, when_local, tzinfo=tz)

