from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Optional


class Priority(StrEnum):
    URGENT = "urgent"
    IMPORTANT = "important"
    OPTIONAL = "optional"


class TaskStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class User:
    telegram_user_id: int
    timezone: str  # IANA TZ, e.g. "Europe/Moscow"
    morning_prompt_time: time  # local time
    evening_check_time: time  # local time


@dataclass(frozen=True)
class Task:
    id: int
    telegram_user_id: int
    title: str
    created_at: datetime
    day: date
    estimated_minutes: int
    due_local_time: Optional[time]
    priority: Priority
    status: TaskStatus


@dataclass(frozen=True)
class ScheduledBlock:
    task_id: int
    title: str
    start_local: datetime
    end_local: datetime
    priority: Priority

