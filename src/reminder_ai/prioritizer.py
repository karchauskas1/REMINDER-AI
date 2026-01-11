from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional, Sequence

from zoneinfo import ZoneInfo

from .models import Priority


@dataclass(frozen=True)
class TaskDraft:
    title: str
    estimated_minutes: int
    due_local_time: Optional[time]


class Prioritizer:
    async def prioritize(
        self, *, drafts: Sequence[TaskDraft], today: date, tz: ZoneInfo
    ) -> list[Priority]:
        raise NotImplementedError


class HeuristicPrioritizer(Prioritizer):
    """
    Deterministic fallback when no AI key is configured.
    - urgent: deadlines today soon, or keywords like "срочно", "дедлайн", "сдать"
    - important: work/health/finance keywords
    - optional: everything else
    """

    _urgent_kw = re.compile(
        r"(?i)\b(срочно|дедлайн|сдать|звонок|созвон|встреча|суд|налог|оплатить)\b"
    )
    _important_kw = re.compile(
        r"(?i)\b(проект|отч[её]т|работа|спорт|тренировк|врач|уч[её]ба|плат[её]ж|сч[её]т)\b"
    )

    async def prioritize(
        self, *, drafts: Sequence[TaskDraft], today: date, tz: ZoneInfo
    ) -> list[Priority]:
        now_local = datetime.now(tz)

        out: list[Priority] = []
        for d in drafts:
            text = d.title.strip()

            if d.due_local_time is not None:
                due_dt = datetime.combine(today, d.due_local_time, tzinfo=tz)
                mins_left = int((due_dt - now_local).total_seconds() // 60)
                if mins_left <= 180:  # within 3 hours (or already late)
                    out.append(Priority.URGENT)
                    continue

            if self._urgent_kw.search(text):
                out.append(Priority.URGENT)
            elif self._important_kw.search(text):
                out.append(Priority.IMPORTANT)
            else:
                out.append(Priority.OPTIONAL)
        return out


class OpenAIPrioritizer(Prioritizer):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key
        self.model = model

    async def prioritize(
        self, *, drafts: Sequence[TaskDraft], today: date, tz: ZoneInfo
    ) -> list[Priority]:
        # Lazy import to keep dependency optional at runtime
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key)
        items = []
        for i, d in enumerate(drafts, start=1):
            due = d.due_local_time.isoformat(timespec="minutes") if d.due_local_time else ""
            items.append(f"{i}. {d.title} | est={d.estimated_minutes}m | due={due}")
        prompt = "\n".join(items)

        system = (
            "You are a day planner. Classify each task as one of: "
            "urgent, important, optional. Return ONLY a JSON array of strings, "
            "same length and order as input."
        )

        resp = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Today is {today.isoformat()} in timezone {tz.key}.\nTasks:\n{prompt}",
                },
            ],
            temperature=0,
        )
        text = resp.choices[0].message.content or "[]"
        return _parse_priority_json_array(text, expected_len=len(drafts))


def _parse_priority_json_array(text: str, expected_len: int) -> list[Priority]:
    """
    Very small, forgiving parser: we expect ["urgent","important",...]
    If anything fails, fallback to all IMPORTANT.
    """
    try:
        import json

        arr = json.loads(text)
        if not isinstance(arr, list) or len(arr) != expected_len:
            raise ValueError("bad length/type")
        out: list[Priority] = []
        for x in arr:
            if not isinstance(x, str):
                raise ValueError("non-string")
            x2 = x.strip().lower()
            out.append(Priority(x2))
        return out
    except Exception:
        return [Priority.IMPORTANT for _ in range(expected_len)]


def build_prioritizer_from_env() -> Prioritizer:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    if key:
        return OpenAIPrioritizer(api_key=key, model=model)
    return HeuristicPrioritizer()

