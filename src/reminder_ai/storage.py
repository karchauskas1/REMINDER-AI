from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable, Optional

from .models import Priority, Task, TaskStatus, User


def _to_iso_dt(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _from_iso_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _to_iso_date(d: date) -> str:
    return d.isoformat()


def _from_iso_date(s: str) -> date:
    return date.fromisoformat(s)


def _to_iso_time(t: Optional[time]) -> Optional[str]:
    if t is None:
        return None
    return t.isoformat(timespec="minutes")


def _from_iso_time(s: Optional[str]) -> Optional[time]:
    if s is None:
        return None
    return time.fromisoformat(s)


class Storage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._init()

    @contextmanager
    def _conn(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                  telegram_user_id INTEGER PRIMARY KEY,
                  timezone TEXT NOT NULL,
                  morning_prompt_time TEXT NOT NULL,
                  evening_check_time TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_state (
                  telegram_user_id INTEGER PRIMARY KEY,
                  last_morning_sent_day TEXT NULL,
                  last_evening_sent_day TEXT NULL,
                  FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  telegram_user_id INTEGER NOT NULL,
                  title TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  day TEXT NOT NULL,
                  estimated_minutes INTEGER NOT NULL,
                  due_local_time TEXT NULL,
                  priority TEXT NOT NULL,
                  status TEXT NOT NULL,
                  FOREIGN KEY (telegram_user_id) REFERENCES users(telegram_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_user_day
                ON tasks(telegram_user_id, day)
                """
            )

    def list_user_ids(self) -> list[int]:
        with self._conn() as conn:
            rows = conn.execute("SELECT telegram_user_id FROM users").fetchall()
        return [int(r["telegram_user_id"]) for r in rows]

    def get_last_sent_days(self, telegram_user_id: int) -> tuple[Optional[date], Optional[date]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_morning_sent_day, last_evening_sent_day FROM user_state WHERE telegram_user_id=?",
                (telegram_user_id,),
            ).fetchone()
        if row is None:
            return (None, None)
        lm = _from_iso_date(row["last_morning_sent_day"]) if row["last_morning_sent_day"] else None
        le = _from_iso_date(row["last_evening_sent_day"]) if row["last_evening_sent_day"] else None
        return (lm, le)

    def set_last_morning_sent_day(self, telegram_user_id: int, day: date) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_state(telegram_user_id, last_morning_sent_day, last_evening_sent_day)
                VALUES(?, ?, NULL)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                  last_morning_sent_day=excluded.last_morning_sent_day
                """,
                (telegram_user_id, _to_iso_date(day)),
            )

    def set_last_evening_sent_day(self, telegram_user_id: int, day: date) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_state(telegram_user_id, last_morning_sent_day, last_evening_sent_day)
                VALUES(?, NULL, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                  last_evening_sent_day=excluded.last_evening_sent_day
                """,
                (telegram_user_id, _to_iso_date(day)),
            )

    def upsert_user(self, user: User) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO users(telegram_user_id, timezone, morning_prompt_time, evening_check_time)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                  timezone=excluded.timezone,
                  morning_prompt_time=excluded.morning_prompt_time,
                  evening_check_time=excluded.evening_check_time
                """,
                (
                    user.telegram_user_id,
                    user.timezone,
                    _to_iso_time(user.morning_prompt_time),
                    _to_iso_time(user.evening_check_time),
                ),
            )

    def get_user(self, telegram_user_id: int) -> Optional[User]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_user_id=?",
                (telegram_user_id,),
            ).fetchone()
        if row is None:
            return None
        return User(
            telegram_user_id=int(row["telegram_user_id"]),
            timezone=str(row["timezone"]),
            morning_prompt_time=time.fromisoformat(row["morning_prompt_time"]),
            evening_check_time=time.fromisoformat(row["evening_check_time"]),
        )

    def add_task(
        self,
        telegram_user_id: int,
        title: str,
        created_at: datetime,
        day: date,
        estimated_minutes: int,
        due_local_time: Optional[time],
        priority: Priority,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO tasks(
                  telegram_user_id, title, created_at, day,
                  estimated_minutes, due_local_time, priority, status
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    title,
                    _to_iso_dt(created_at),
                    _to_iso_date(day),
                    int(estimated_minutes),
                    _to_iso_time(due_local_time),
                    str(priority),
                    str(TaskStatus.PENDING),
                ),
            )
            return int(cur.lastrowid)

    def list_tasks(
        self, telegram_user_id: int, day: date, status: Optional[TaskStatus] = None
    ) -> list[Task]:
        with self._conn() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE telegram_user_id=? AND day=? ORDER BY id ASC",
                    (telegram_user_id, _to_iso_date(day)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE telegram_user_id=? AND day=? AND status=?
                    ORDER BY id ASC
                    """,
                    (telegram_user_id, _to_iso_date(day), str(status)),
                ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def update_task_status(
        self, telegram_user_id: int, task_id: int, status: TaskStatus
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE tasks SET status=?
                WHERE telegram_user_id=? AND id=?
                """,
                (str(status), telegram_user_id, task_id),
            )

    def delete_tasks_for_day(self, telegram_user_id: int, day: date) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM tasks WHERE telegram_user_id=? AND day=?",
                (telegram_user_id, _to_iso_date(day)),
            )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=int(row["id"]),
            telegram_user_id=int(row["telegram_user_id"]),
            title=str(row["title"]),
            created_at=_from_iso_dt(str(row["created_at"])),
            day=_from_iso_date(str(row["day"])),
            estimated_minutes=int(row["estimated_minutes"]),
            due_local_time=_from_iso_time(row["due_local_time"]),
            priority=Priority(str(row["priority"])),
            status=TaskStatus(str(row["status"])),
        )

