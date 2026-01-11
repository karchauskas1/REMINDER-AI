from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    AIORateLimiter,
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .jobs import carryover_priority, due_for_daily_send
from .models import Priority, TaskStatus, User
from .parsing import parse_task_line
from .planner import PlanTask, PlanWindow, build_schedule
from .prioritizer import TaskDraft, build_prioritizer_from_env
from .storage import Storage

log = logging.getLogger("reminder_ai")


HELP = (
    "Команды:\n"
    "/start — регистрация\n"
    "/timezone Europe/Moscow — установить часовой пояс\n"
    "/plan — попросить задачи и построить расписание\n"
    "/tasks — показать задачи на сегодня\n"
    "/done <id> — отметить выполненной\n"
    "/skip <id> — пропустить\n"
)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _format_priority(p: Priority) -> str:
    return {Priority.URGENT: "срочно", Priority.IMPORTANT: "важно", Priority.OPTIONAL: "необязательно"}[
        p
    ]


class BotApp:
    def __init__(self, db_path: str) -> None:
        self.storage = Storage(db_path)
        self.prioritizer = build_prioritizer_from_env()
        self.scheduler = AsyncIOScheduler()

        # in-memory flag: who is currently entering tasks
        self._awaiting_tasks: set[int] = set()

    def user_or_default(self, user_id: int) -> User:
        u = self.storage.get_user(user_id)
        if u:
            return u
        u = User(
            telegram_user_id=user_id,
            timezone="UTC",
            morning_prompt_time=time(8, 0),
            evening_check_time=time(20, 0),
        )
        self.storage.upsert_user(u)
        return u

    def _tz(self, user_id: int) -> ZoneInfo:
        return ZoneInfo(self.user_or_default(user_id).timezone)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.effective_user and update.message
        user_id = int(update.effective_user.id)
        self.user_or_default(user_id)
        await update.message.reply_text(
            "Привет! Я помогу спланировать день.\n\n"
            "1) Укажи часовой пояс командой /timezone Europe/Moscow\n"
            "2) Вызови /plan чтобы внести задачи\n\n"
            + HELP
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(HELP)

    async def cmd_timezone(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.effective_user and update.message
        user_id = int(update.effective_user.id)
        if not context.args:
            await update.message.reply_text("Укажи IANA-таймзону, например: /timezone Europe/Moscow")
            return
        tz_name = context.args[0].strip()
        try:
            ZoneInfo(tz_name)
        except Exception:
            await update.message.reply_text("Не понял таймзону. Пример: Europe/Moscow, Europe/Kyiv, UTC")
            return

        u = self.user_or_default(user_id)
        u2 = User(
            telegram_user_id=u.telegram_user_id,
            timezone=tz_name,
            morning_prompt_time=u.morning_prompt_time,
            evening_check_time=u.evening_check_time,
        )
        self.storage.upsert_user(u2)
        await update.message.reply_text(f"Ок, таймзона установлена: {tz_name}")

    async def cmd_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.effective_user and update.message
        user_id = int(update.effective_user.id)
        self.user_or_default(user_id)
        self._awaiting_tasks.add(user_id)
        await update.message.reply_text(
            "Напиши задачи на сегодня, по одной в строке.\n"
            "Формат (необязательно): `@HH:MM` дедлайн и `30m`/`2h` длительность.\n"
            "Пример:\n"
            "Созвон с Петей @10:30 30m\n"
            "Отчёт @17:00 2h\n\n"
            "Отправь одним сообщением.",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.effective_user and update.message
        user_id = int(update.effective_user.id)
        tz = self._tz(user_id)
        today = datetime.now(tz).date()
        tasks = self.storage.list_tasks(user_id, today)
        if not tasks:
            await update.message.reply_text("На сегодня задач нет. Используй /plan.")
            return
        lines = []
        for t in tasks:
            due = f" @{t.due_local_time.isoformat(timespec='minutes')}" if t.due_local_time else ""
            lines.append(f"{t.id}. [{t.status}] ({_format_priority(t.priority)}) {t.title}{due} ~{t.estimated_minutes}m")
        await update.message.reply_text("\n".join(lines))

    async def cmd_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.effective_user and update.message
        user_id = int(update.effective_user.id)
        if not context.args:
            await update.message.reply_text("Использование: /done <id>")
            return
        task_id = int(context.args[0])
        self.storage.update_task_status(user_id, task_id, TaskStatus.DONE)
        await update.message.reply_text("Отмечено как выполнено.")

    async def cmd_skip(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.effective_user and update.message
        user_id = int(update.effective_user.id)
        if not context.args:
            await update.message.reply_text("Использование: /skip <id>")
            return
        task_id = int(context.args[0])
        self.storage.update_task_status(user_id, task_id, TaskStatus.SKIPPED)
        await update.message.reply_text("Ок, пропущено.")

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        assert update.effective_user and update.message
        user_id = int(update.effective_user.id)

        if user_id not in self._awaiting_tasks:
            return

        tz = self._tz(user_id)
        now_local = datetime.now(tz)
        today = now_local.date()

        lines = [x.strip() for x in (update.message.text or "").splitlines()]
        parsed = [parse_task_line(x) for x in lines]
        parsed = [p for p in parsed if p is not None]
        if not parsed:
            await update.message.reply_text("Не нашёл задач. Попробуй ещё раз (по одной в строке).")
            return

        drafts = [TaskDraft(title=p.title, estimated_minutes=p.estimated_minutes, due_local_time=p.due_local_time) for p in parsed]
        priorities = await self.prioritizer.prioritize(drafts=drafts, today=today, tz=tz)

        # reset tasks for today, then insert
        self.storage.delete_tasks_for_day(user_id, today)
        task_ids: list[int] = []
        for p, pr in zip(parsed, priorities, strict=True):
            task_id = self.storage.add_task(
                telegram_user_id=user_id,
                title=p.title,
                created_at=now_local,
                day=today,
                estimated_minutes=p.estimated_minutes,
                due_local_time=p.due_local_time,
                priority=pr,
            )
            task_ids.append(task_id)

        tasks = self.storage.list_tasks(user_id, today, status=TaskStatus.PENDING)
        plan_tasks = [
            PlanTask(
                task_id=t.id,
                title=t.title,
                estimated_minutes=t.estimated_minutes,
                due_local_time=t.due_local_time,
                priority=t.priority,
            )
            for t in tasks
        ]
        blocks = build_schedule(day=today, tz=tz, tasks=plan_tasks, window=PlanWindow())

        if not blocks:
            await update.message.reply_text("Задачи сохранены, но я не смог составить расписание (окно слишком маленькое?).")
        else:
            msg_lines = ["Вот расписание на сегодня:"]
            for b in blocks:
                msg_lines.append(
                    f"- {b.start_local.strftime('%H:%M')}-{b.end_local.strftime('%H:%M')} "
                    f"({ _format_priority(b.priority) }) {b.title} (id {b.task_id})"
                )
            await update.message.reply_text("\n".join(msg_lines))

        # schedule reminders for today blocks
        self._schedule_today_reminders(
            telegram_user_id=user_id,
            tz=tz,
            blocks=blocks,
            app=context.application,
        )

        self._awaiting_tasks.discard(user_id)
        await update.message.reply_text("Напоминания включены. /tasks чтобы посмотреть список.")

    def _schedule_today_reminders(
        self,
        *,
        telegram_user_id: int,
        tz: ZoneInfo,
        blocks: list,
        app: Application,
    ) -> None:
        # remove existing jobs for this user for today
        prefix = f"reminder:{telegram_user_id}:"
        for job in list(self.scheduler.get_jobs()):
            if job.id.startswith(prefix):
                job.remove()

        now = datetime.now(tz)
        for b in blocks:
            # notify 10 minutes before and at start
            for kind, when in [
                ("before", b.start_local - timedelta(minutes=10)),
                ("start", b.start_local),
            ]:
                if when <= now:
                    continue
                job_id = f"{prefix}{b.task_id}:{kind}"
                self.scheduler.add_job(
                    func=lambda chat_id=telegram_user_id, block=b, k=kind: asyncio.create_task(
                        app.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"Напоминание ({'через 10 минут' if k=='before' else 'сейчас'}): "
                                f"{block.title} (id {block.task_id})"
                            ),
                        )
                    ),
                    trigger="date",
                    run_date=when,
                    id=job_id,
                    replace_existing=True,
                )

    def register_recurring_jobs(self, app: Application) -> None:
        # Every minute we can resync per-user morning/evening via cron triggers.
        # For simplicity: create a daily UTC tick and inside compute per-user local times.
        self.scheduler.add_job(
            func=lambda: asyncio.create_task(self._daily_tick(app)),
            trigger=CronTrigger(minute="*/5"),  # light periodic sync
            id="daily-tick",
            replace_existing=True,
        )

    async def _daily_tick(self, app: Application) -> None:
        # For each user, if it's time to send morning/evening prompt, send once per day.
        for user_id in self.storage.list_user_ids():
            u = self.user_or_default(user_id)
            tz = ZoneInfo(u.timezone)
            now = datetime.now(tz)
            today = now.date()
            last_morning, last_evening = self.storage.get_last_sent_days(user_id)

            m = due_for_daily_send(
                now=now,
                today=today,
                when_local=u.morning_prompt_time,
                tz=tz,
                last_sent_day=last_morning,
            )
            if m.due_now:
                await app.bot.send_message(
                    chat_id=user_id,
                    text="Доброе утро! Какие ключевые задачи на сегодня? Нажми /plan.",
                )
                self.storage.set_last_morning_sent_day(user_id, today)

            e = due_for_daily_send(
                now=now,
                today=today,
                when_local=u.evening_check_time,
                tz=tz,
                last_sent_day=last_evening,
            )
            if e.due_now:
                await self._evening_check(app, user_id, tz)
                self.storage.set_last_evening_sent_day(user_id, today)

    async def _evening_check(self, app: Application, user_id: int, tz: ZoneInfo) -> None:
        today = datetime.now(tz).date()
        pending = self.storage.list_tasks(user_id, today, status=TaskStatus.PENDING)
        if not pending:
            await app.bot.send_message(chat_id=user_id, text="Отлично! Все задачи на сегодня закрыты. До завтра.")
            return
        tomorrow = today + timedelta(days=1)
        already = self.storage.list_tasks(user_id, tomorrow)
        if not already:
            now_local = datetime.now(tz)
            for t in pending:
                self.storage.add_task(
                    telegram_user_id=user_id,
                    title=t.title,
                    created_at=now_local,
                    day=tomorrow,
                    estimated_minutes=t.estimated_minutes,
                    due_local_time=t.due_local_time,
                    priority=carryover_priority(t.priority),
                )

        lines = ["Как прошёл день? Остались невыполненные задачи:"]
        for t in pending:
            lines.append(f"- {t.id}. ({_format_priority(t.priority)}) {t.title}")
        if not already:
            lines.append("Я перенёс эти задачи на завтра (с повышенным приоритетом).")
        else:
            lines.append("На завтра уже есть план — я ничего не переносил автоматически.")
        lines.append("Отметь выполненные: /done <id> или пропусти: /skip <id>. Завтра можно снова /plan.")
        await app.bot.send_message(chat_id=user_id, text="\n".join(lines))


def build_application(bot_app: BotApp) -> Application:
    token = _env("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is required")

    app = (
        Application.builder()
        .token(token)
        .rate_limiter(AIORateLimiter())
        .build()
    )

    app.add_handler(CommandHandler("start", bot_app.cmd_start))
    app.add_handler(CommandHandler("help", bot_app.cmd_help))
    app.add_handler(CommandHandler("timezone", bot_app.cmd_timezone))
    app.add_handler(CommandHandler("plan", bot_app.cmd_plan))
    app.add_handler(CommandHandler("tasks", bot_app.cmd_tasks))
    app.add_handler(CommandHandler("done", bot_app.cmd_done))
    app.add_handler(CommandHandler("skip", bot_app.cmd_skip))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_app.on_text))

    bot_app.register_recurring_jobs(app)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    db_path = _env("DB_PATH", "reminder_ai.sqlite3")
    bot_app = BotApp(db_path=db_path)
    app = build_application(bot_app)
    bot_app.scheduler.start()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

