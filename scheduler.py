from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import services as svc

logger = logging.getLogger(__name__)


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(lambda: asyncio.create_task(check_reminders(bot)), "interval", minutes=5, id="check_reminders", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started")
    return scheduler


async def safe_send(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Cannot send message to %s: %s", chat_id, exc)
    except Exception:
        logger.exception("Unexpected send error")


async def check_reminders(bot: Bot) -> None:
    users = await svc.get_users_for_scheduler()
    for user in users:
        try:
            await process_user_reminders(bot, user)
        except Exception:
            logger.exception("Reminder processing failed for user_id=%s", user["id"])


async def process_user_reminders(bot: Bot, user) -> None:
    tz = ZoneInfo(user["timezone"])
    now = datetime.now(tz)
    current_hhmm = f"{now.hour:02d}:00"
    today = now.date()

    # Все пользовательские уведомления отправляются в выбранный час дайджеста.
    if current_hhmm != user["digest_time"]:
        return

    await send_template_work_reminders(bot, user, today)
    await send_daily_tomorrow_digest(bot, user, today)
    await send_period_digest_if_needed(bot, user, today)


async def send_template_work_reminders(bot: Bot, user, today) -> None:
    max_days = max(max(days) for days in svc.REMINDER_TEMPLATES.values())
    rows = await svc.works_between(user["id"], today, today + timedelta(days=max_days))
    for w in rows:
        deadline = datetime.fromisoformat(w["deadline_date"]).date()
        days_left = (deadline - today).days
        allowed_days = svc.REMINDER_TEMPLATES.get(w["work_type"], [1])
        if days_left not in allowed_days:
            continue

        key = f"work_{days_left}_days_{w['deadline_date']}"
        if await svc.was_sent(user["id"], w["id"], key):
            continue

        when = "завтра" if days_left == 1 else f"через {days_left} дн."
        text = (
            "🔔 Напоминание\n\n"
            f"Дедлайн {when}:\n\n"
            f"{svc.emphasize_type(w['work_type'])}\n"
            f"Предмет: {w['subject_title']}\n"
            f"Работа: {w['title']}\n"
            f"Дата сдачи: {svc.format_ru_date(w['deadline_date'])}"
        )
        await safe_send(bot, user["telegram_id"], text)
        await svc.mark_sent(user["id"], w["id"], key)


async def send_daily_tomorrow_digest(bot: Bot, user, today) -> None:
    if not user["daily_tomorrow_digest_enabled"]:
        return
    tomorrow = today + timedelta(days=1)
    key = f"daily_tomorrow_{tomorrow.isoformat()}"
    if await svc.was_sent(user["id"], None, key):
        return

    rows = await svc.works_due_on(user["id"], tomorrow)
    text = svc.format_digest(
        f"📌 Дайджест на завтра ({svc.format_ru_date(tomorrow)})",
        rows,
        "✅ На завтра работ нет.",
    )
    await safe_send(bot, user["telegram_id"], text)
    await svc.mark_sent(user["id"], None, key)


async def send_period_digest_if_needed(bot: Bot, user, today) -> None:
    if user["frequency"] == "weekly":
        if today.weekday() != 0:
            return
        iso_year, iso_week, _ = today.isocalendar()
        key = f"weekly_digest_{iso_year}-W{iso_week}"
        if await svc.was_sent(user["id"], None, key):
            return
        start = today
        end = today + timedelta(days=6)
        rows = await svc.works_between(user["id"], start, end)
        text = svc.format_digest(
            f"📅 На этой неделе будут работы ({svc.format_ru_date(start)} — {svc.format_ru_date(end)})",
            rows,
            "✅ На этой неделе работ нет.",
        )
        await safe_send(bot, user["telegram_id"], text)
        await svc.mark_sent(user["id"], None, key)
        return

    if user["frequency"] == "monthly":
        if today.day != 1:
            return
        key = f"monthly_digest_{today.year}-{today.month:02d}"
        if await svc.was_sent(user["id"], None, key):
            return
        start = today
        end = today + timedelta(days=29)
        rows = await svc.works_between(user["id"], start, end)
        text = svc.format_digest(
            f"📅 В ближайшие 30 дней будут работы ({svc.format_ru_date(start)} — {svc.format_ru_date(end)})",
            rows,
            "✅ В ближайшие 30 дней работ нет.",
        )
        await safe_send(bot, user["telegram_id"], text)
        await svc.mark_sent(user["id"], None, key)
