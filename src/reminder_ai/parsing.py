from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from typing import Optional


@dataclass(frozen=True)
class ParsedTaskInput:
    title: str
    estimated_minutes: int
    due_local_time: Optional[time]


_RE_MINUTES = re.compile(r"(?i)\b(\d+)\s*(m|min|мин)\b")
_RE_HOURS = re.compile(r"(?i)\b(\d+)\s*(h|hr|час|ч)\b")
_RE_DUE = re.compile(r"(?i)(?:^|\s)@(\d{1,2}):(\d{2})\b")


def parse_task_line(line: str) -> Optional[ParsedTaskInput]:
    """
    Accepts lines like:
    - "Созвон с командой @10:30 30m"
    - "Сдать отчёт @17:00 2h"
    - "Почта 20m"
    If no duration is present, defaults to 30 minutes.
    """
    raw = line.strip()
    if not raw:
        return None

    due_match = _RE_DUE.search(raw)
    due: Optional[time] = None
    if due_match:
        hh = int(due_match.group(1))
        mm = int(due_match.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            due = time(hour=hh, minute=mm)
        raw = _RE_DUE.sub(" ", raw).strip()

    minutes = 0
    for m in _RE_MINUTES.finditer(raw):
        minutes += int(m.group(1))
    for h in _RE_HOURS.finditer(raw):
        minutes += int(h.group(1)) * 60

    title = _RE_MINUTES.sub(" ", raw)
    title = _RE_HOURS.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" -\t")

    if not title:
        return None
    if minutes <= 0:
        minutes = 30

    return ParsedTaskInput(title=title, estimated_minutes=minutes, due_local_time=due)

