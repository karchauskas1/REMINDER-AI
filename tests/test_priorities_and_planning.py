from __future__ import annotations

from datetime import date, datetime, time

from zoneinfo import ZoneInfo

from reminder_ai.jobs import carryover_priority, due_for_daily_send
from reminder_ai.models import Priority
from reminder_ai.planner import PlanTask, build_schedule
from reminder_ai.prioritizer import HeuristicPrioritizer, TaskDraft


def test_heuristic_priorities_deadline_is_urgent() -> None:
    import asyncio

    tz = ZoneInfo("UTC")
    today = date(2026, 1, 11)
    pr = HeuristicPrioritizer()

    # simulate "now" by picking due time near typical now; heuristic uses real clock,
    # so we test keyword route deterministically instead.
    drafts = [
        TaskDraft(title="Сдать отчет срочно", estimated_minutes=60, due_local_time=None),
        TaskDraft(title="Почта", estimated_minutes=20, due_local_time=None),
    ]
    out = asyncio.run(pr.prioritize(drafts=drafts, today=today, tz=tz))
    assert out[0] == Priority.URGENT
    assert out[1] in {Priority.IMPORTANT, Priority.OPTIONAL}


def test_schedule_orders_urgent_before_optional() -> None:
    tz = ZoneInfo("UTC")
    day = date(2026, 1, 11)
    tasks = [
        PlanTask(task_id=1, title="Optional", estimated_minutes=30, due_local_time=None, priority=Priority.OPTIONAL),
        PlanTask(task_id=2, title="Urgent", estimated_minutes=30, due_local_time=None, priority=Priority.URGENT),
        PlanTask(task_id=3, title="Important", estimated_minutes=30, due_local_time=None, priority=Priority.IMPORTANT),
    ]
    blocks = build_schedule(day=day, tz=tz, tasks=tasks)
    assert [b.task_id for b in blocks][:3] == [2, 3, 1]


def test_due_for_daily_send_only_once_per_day() -> None:
    tz = ZoneInfo("UTC")
    today = date(2026, 1, 11)
    when = time(8, 0)
    now = datetime(2026, 1, 11, 8, 5, tzinfo=tz)

    d1 = due_for_daily_send(now=now, today=today, when_local=when, tz=tz, last_sent_day=None, window_minutes=10)
    assert d1.due_now is True

    d2 = due_for_daily_send(now=now, today=today, when_local=when, tz=tz, last_sent_day=today, window_minutes=10)
    assert d2.due_now is False


def test_carryover_priority_bumps() -> None:
    assert carryover_priority(Priority.OPTIONAL) == Priority.IMPORTANT
    assert carryover_priority(Priority.IMPORTANT) == Priority.URGENT
    assert carryover_priority(Priority.URGENT) == Priority.URGENT

