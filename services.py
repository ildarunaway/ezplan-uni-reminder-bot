from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite

from config import get_settings
from db import connect, execute, fetchall, fetchone, utc_now

settings = get_settings()

WORK_TYPES = [
    "Домашняя работа",
    "Контрольная",
    "Лабораторная",
    "Экзамен",
    "Реферат",
    "Проект",
    "Другое",
]

WORK_TYPE_CODES = {
    "hw": "Домашняя работа",
    "test": "Контрольная",
    "lab": "Лабораторная",
    "exam": "Экзамен",
    "essay": "Реферат",
    "project": "Проект",
    "other": "Другое",
}
WORK_TYPE_TO_CODE = {v: k for k, v in WORK_TYPE_CODES.items()}

REMINDER_TEMPLATES = {
    "Экзамен": [14, 7, 3, 1],
    "Контрольная": [7, 3, 1],
    "Проект": [7, 3, 1],
    "Лабораторная": [3, 1],
    "Реферат": [3, 1],
    "Домашняя работа": [1],
    "Другое": [1],
}

RUSSIAN_TIMEZONES = {
    "Москва UTC+3": "Europe/Moscow",
    "Самара UTC+4": "Europe/Samara",
    "Екатеринбург UTC+5": "Asia/Yekaterinburg",
    "Омск UTC+6": "Asia/Omsk",
    "Красноярск UTC+7": "Asia/Krasnoyarsk",
    "Иркутск UTC+8": "Asia/Irkutsk",
    "Якутск UTC+9": "Asia/Yakutsk",
    "Владивосток UTC+10": "Asia/Vladivostok",
    "Магадан UTC+11": "Asia/Magadan",
    "Камчатка UTC+12": "Asia/Kamchatka",
}
TIMEZONE_TO_LABEL = {v: k for k, v in RUSSIAN_TIMEZONES.items()}

PAGE_SIZE = 8


def today_in_tz(tz_name: str) -> date:
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return datetime.now(ZoneInfo(settings.default_timezone)).date()


def parse_ru_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def format_ru_date(value: str | date) -> str:
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return value.strftime("%d.%m.%Y")


def days_word(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "дней"
    if n % 10 == 1:
        return "день"
    if 2 <= n % 10 <= 4:
        return "дня"
    return "дней"


def work_status_label(work: aiosqlite.Row, today: date) -> str:
    if work["status"] == "completed":
        return "✅ Выполнена"
    deadline = date.fromisoformat(work["deadline_date"])
    if deadline < today:
        overdue = (today - deadline).days
        return f"❗ Просрочена на {overdue} {days_word(overdue)}"
    return "⏳ Активна"


def emphasize_type(work_type: str) -> str:
    if work_type in {"Экзамен", "Контрольная"}:
        return f"**{work_type}**"
    return work_type


async def ensure_user(tg_user) -> aiosqlite.Row:
    now = utc_now()
    user = await fetchone("SELECT * FROM users WHERE telegram_id = ?", (tg_user.id,))
    if user:
        await execute(
            """
            UPDATE users
            SET username = ?, first_name = ?, last_name = ?, updated_at = ?
            WHERE telegram_id = ?
            """,
            (tg_user.username, tg_user.first_name, tg_user.last_name, now, tg_user.id),
        )
        await ensure_reminder_settings(user["id"])
        return await fetchone("SELECT * FROM users WHERE telegram_id = ?", (tg_user.id,))

    user_id = await execute(
        """
        INSERT INTO users (telegram_id, username, first_name, last_name, timezone, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name, settings.default_timezone, now, now),
    )
    await ensure_reminder_settings(user_id)
    return await fetchone("SELECT * FROM users WHERE id = ?", (user_id,))


async def ensure_reminder_settings(user_id: int) -> None:
    now = utc_now()
    existing = await fetchone("SELECT id FROM reminder_settings WHERE user_id = ?", (user_id,))
    if not existing:
        await execute(
            """
            INSERT INTO reminder_settings
            (user_id, is_enabled, digest_time, frequency, daily_tomorrow_digest_enabled, created_at, updated_at)
            VALUES (?, 1, '09:00', 'weekly', 1, ?, ?)
            """,
            (user_id, now, now),
        )


async def get_user_by_telegram_id(telegram_id: int) -> aiosqlite.Row | None:
    return await fetchone("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))


async def update_university(user_id: int, university: str) -> None:
    await execute("UPDATE users SET university = ?, updated_at = ? WHERE id = ?", (university, utc_now(), user_id))


async def update_timezone(user_id: int, timezone: str) -> None:
    await execute("UPDATE users SET timezone = ?, updated_at = ? WHERE id = ?", (timezone, utc_now(), user_id))


async def get_subjects(user_id: int) -> list[aiosqlite.Row]:
    return await fetchall("SELECT * FROM subjects WHERE user_id = ? ORDER BY title COLLATE NOCASE", (user_id,))


async def get_subject(user_id: int, subject_id: int) -> aiosqlite.Row | None:
    return await fetchone("SELECT * FROM subjects WHERE user_id = ? AND id = ?", (user_id, subject_id))


async def create_subject(user_id: int, title: str) -> tuple[bool, str]:
    title = " ".join(title.strip().split())
    if not (2 <= len(title) <= 64):
        return False, "Название предмета должно быть от 2 до 64 символов."
    now = utc_now()
    try:
        await execute(
            "INSERT INTO subjects (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, title, now, now),
        )
        return True, f"Предмет «{title}» добавлен."
    except Exception:
        return False, "Такой предмет уже есть или произошла ошибка сохранения."


async def rename_subject(user_id: int, subject_id: int, title: str) -> tuple[bool, str]:
    title = " ".join(title.strip().split())
    if not (2 <= len(title) <= 64):
        return False, "Название предмета должно быть от 2 до 64 символов."
    try:
        await execute(
            "UPDATE subjects SET title = ?, updated_at = ? WHERE user_id = ? AND id = ?",
            (title, utc_now(), user_id, subject_id),
        )
        return True, f"Предмет переименован в «{title}»."
    except Exception:
        return False, "Не удалось переименовать предмет. Возможно, такой предмет уже есть."


async def delete_subject(user_id: int, subject_id: int) -> None:
    await execute("DELETE FROM subjects WHERE user_id = ? AND id = ?", (user_id, subject_id))


async def count_subject_works(user_id: int, subject_id: int, today: date) -> dict[str, int]:
    rows = await fetchall("SELECT status, deadline_date FROM works WHERE user_id = ? AND subject_id = ?", (user_id, subject_id))
    result = {"total": len(rows), "active": 0, "completed": 0, "overdue": 0}
    for w in rows:
        if w["status"] == "completed":
            result["completed"] += 1
        elif date.fromisoformat(w["deadline_date"]) < today:
            result["overdue"] += 1
        else:
            result["active"] += 1
    return result


async def create_works(user_id: int, subject_id: int, work_type: str, items: list[dict]) -> None:
    now = utc_now()
    params = []
    for item in items:
        params.append((user_id, subject_id, work_type, item["title"], item["deadline_date"], "active", now, now))
    async with await connect() as db:
        await db.executemany(
            """
            INSERT INTO works (user_id, subject_id, work_type, title, deadline_date, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        await db.commit()


async def get_work(user_id: int, work_id: int) -> aiosqlite.Row | None:
    return await fetchone(
        """
        SELECT w.*, s.title AS subject_title
        FROM works w
        JOIN subjects s ON s.id = w.subject_id
        WHERE w.user_id = ? AND w.id = ?
        """,
        (user_id, work_id),
    )


async def set_work_completed(user_id: int, work_id: int) -> None:
    await execute(
        "UPDATE works SET status = 'completed', completed_at = ?, updated_at = ? WHERE user_id = ? AND id = ?",
        (utc_now(), utc_now(), user_id, work_id),
    )


async def set_work_active(user_id: int, work_id: int) -> None:
    await execute(
        "UPDATE works SET status = 'active', completed_at = NULL, updated_at = ? WHERE user_id = ? AND id = ?",
        (utc_now(), user_id, work_id),
    )


async def rename_work(user_id: int, work_id: int, title: str) -> tuple[bool, str]:
    title = " ".join(title.strip().split())
    if not (2 <= len(title) <= 128):
        return False, "Название работы должно быть от 2 до 128 символов."
    await execute("UPDATE works SET title = ?, updated_at = ? WHERE user_id = ? AND id = ?", (title, utc_now(), user_id, work_id))
    return True, f"Работа переименована в «{title}»."


async def reschedule_work(user_id: int, work_id: int, new_date: date) -> None:
    await execute(
        "UPDATE works SET deadline_date = ?, updated_at = ? WHERE user_id = ? AND id = ?",
        (new_date.isoformat(), utc_now(), user_id, work_id),
    )


async def delete_work(user_id: int, work_id: int) -> None:
    await execute("DELETE FROM works WHERE user_id = ? AND id = ?", (user_id, work_id))


def _order_sql() -> str:
    return """
    ORDER BY
        CASE
            WHEN w.status = 'active' AND w.deadline_date < ? THEN 1
            WHEN w.status = 'active' AND w.deadline_date >= ? THEN 2
            WHEN w.status = 'completed' THEN 3
            ELSE 4
        END,
        w.deadline_date ASC,
        w.id ASC
    """


async def list_subject_works(user_id: int, subject_id: int, page: int, today: date) -> tuple[list[aiosqlite.Row], int]:
    total_row = await fetchone("SELECT COUNT(*) AS c FROM works WHERE user_id = ? AND subject_id = ?", (user_id, subject_id))
    total = int(total_row["c"])
    offset = page * PAGE_SIZE
    rows = await fetchall(
        f"""
        SELECT w.*, s.title AS subject_title
        FROM works w JOIN subjects s ON s.id = w.subject_id
        WHERE w.user_id = ? AND w.subject_id = ?
        {_order_sql()}
        LIMIT ? OFFSET ?
        """,
        (user_id, subject_id, today.isoformat(), today.isoformat(), PAGE_SIZE, offset),
    )
    return rows, total


async def list_by_status(user_id: int, status_key: str, page: int, today: date) -> tuple[list[aiosqlite.Row], int]:
    if status_key == "overdue":
        where = "w.user_id = ? AND w.status = 'active' AND w.deadline_date < ?"
        params = (user_id, today.isoformat())
    elif status_key == "upcoming":
        where = "w.user_id = ? AND w.status = 'active' AND w.deadline_date >= ?"
        params = (user_id, today.isoformat())
    else:
        where = "w.user_id = ? AND w.status = 'completed'"
        params = (user_id,)

    total_row = await fetchone(f"SELECT COUNT(*) AS c FROM works w WHERE {where}", params)
    total = int(total_row["c"])
    offset = page * PAGE_SIZE
    rows = await fetchall(
        f"""
        SELECT w.*, s.title AS subject_title
        FROM works w JOIN subjects s ON s.id = w.subject_id
        WHERE {where}
        ORDER BY w.deadline_date ASC, w.id ASC
        LIMIT ? OFFSET ?
        """,
        params + (PAGE_SIZE, offset),
    )
    return rows, total


async def list_by_type(user_id: int, work_type: str, page: int, today: date) -> tuple[list[aiosqlite.Row], int]:
    total_row = await fetchone("SELECT COUNT(*) AS c FROM works WHERE user_id = ? AND work_type = ?", (user_id, work_type))
    total = int(total_row["c"])
    offset = page * PAGE_SIZE
    rows = await fetchall(
        f"""
        SELECT w.*, s.title AS subject_title
        FROM works w JOIN subjects s ON s.id = w.subject_id
        WHERE w.user_id = ? AND w.work_type = ?
        {_order_sql()}
        LIMIT ? OFFSET ?
        """,
        (user_id, work_type, today.isoformat(), today.isoformat(), PAGE_SIZE, offset),
    )
    return rows, total


async def quick_view(user_id: int, mode: str, page: int, today: date) -> tuple[list[aiosqlite.Row], int, str]:
    if mode == "today":
        start = end = today
        title = "Сегодня"
        where = "w.user_id = ? AND w.status = 'active' AND w.deadline_date BETWEEN ? AND ?"
        params = (user_id, start.isoformat(), end.isoformat())
    elif mode == "tomorrow":
        start = end = today + timedelta(days=1)
        title = "Завтра"
        where = "w.user_id = ? AND w.status = 'active' AND w.deadline_date BETWEEN ? AND ?"
        params = (user_id, start.isoformat(), end.isoformat())
    elif mode == "7":
        start, end = today, today + timedelta(days=6)
        title = f"На 7 дней: {format_ru_date(start)} — {format_ru_date(end)}"
        where = "w.user_id = ? AND w.status = 'active' AND w.deadline_date BETWEEN ? AND ?"
        params = (user_id, start.isoformat(), end.isoformat())
    elif mode == "30":
        start, end = today, today + timedelta(days=29)
        title = f"На 30 дней: {format_ru_date(start)} — {format_ru_date(end)}"
        where = "w.user_id = ? AND w.status = 'active' AND w.deadline_date BETWEEN ? AND ?"
        params = (user_id, start.isoformat(), end.isoformat())
    else:
        title = "Просроченные"
        where = "w.user_id = ? AND w.status = 'active' AND w.deadline_date < ?"
        params = (user_id, today.isoformat())

    total_row = await fetchone(f"SELECT COUNT(*) AS c FROM works w WHERE {where}", params)
    total = int(total_row["c"])
    offset = page * PAGE_SIZE
    rows = await fetchall(
        f"""
        SELECT w.*, s.title AS subject_title
        FROM works w JOIN subjects s ON s.id = w.subject_id
        WHERE {where}
        ORDER BY w.deadline_date ASC, w.id ASC
        LIMIT ? OFFSET ?
        """,
        params + (PAGE_SIZE, offset),
    )
    return rows, total, title


async def get_statistics(user_id: int, today: date) -> dict:
    works = await fetchall(
        """
        SELECT w.*, s.title AS subject_title
        FROM works w JOIN subjects s ON s.id = w.subject_id
        WHERE w.user_id = ?
        """,
        (user_id,),
    )
    by_type = defaultdict(int)
    by_subject = defaultdict(lambda: {"total": 0, "completed": 0, "active": 0, "overdue": 0})
    total = completed = active = overdue = 0

    for w in works:
        total += 1
        by_type[w["work_type"]] += 1
        subj = by_subject[w["subject_title"]]
        subj["total"] += 1
        if w["status"] == "completed":
            completed += 1
            subj["completed"] += 1
        elif date.fromisoformat(w["deadline_date"]) < today:
            overdue += 1
            subj["overdue"] += 1
        else:
            active += 1
            subj["active"] += 1

    denom = completed + overdue
    completion_percent = round(completed / denom * 100) if denom else None

    subject_stats = {}
    for title, data in by_subject.items():
        denom_s = data["completed"] + data["overdue"]
        data["percent"] = round(data["completed"] / denom_s * 100) if denom_s else None
        subject_stats[title] = dict(data)

    return {
        "total": total,
        "completed": completed,
        "active": active,
        "overdue": overdue,
        "completion_percent": completion_percent,
        "by_type": dict(by_type),
        "by_subject": subject_stats,
    }


async def get_reminder_settings(user_id: int) -> aiosqlite.Row:
    await ensure_reminder_settings(user_id)
    return await fetchone("SELECT * FROM reminder_settings WHERE user_id = ?", (user_id,))


async def toggle_reminders(user_id: int) -> aiosqlite.Row:
    current = await get_reminder_settings(user_id)
    new_value = 0 if current["is_enabled"] else 1
    await execute("UPDATE reminder_settings SET is_enabled = ?, updated_at = ? WHERE user_id = ?", (new_value, utc_now(), user_id))
    return await get_reminder_settings(user_id)


async def set_digest_time(user_id: int, hour: int) -> None:
    await execute("UPDATE reminder_settings SET digest_time = ?, updated_at = ? WHERE user_id = ?", (f"{hour:02d}:00", utc_now(), user_id))


async def set_frequency(user_id: int, frequency: str) -> None:
    await execute("UPDATE reminder_settings SET frequency = ?, updated_at = ? WHERE user_id = ?", (frequency, utc_now(), user_id))


async def get_users_for_scheduler() -> list[aiosqlite.Row]:
    return await fetchall(
        """
        SELECT u.*, rs.is_enabled, rs.digest_time, rs.frequency, rs.daily_tomorrow_digest_enabled
        FROM users u JOIN reminder_settings rs ON rs.user_id = u.id
        WHERE rs.is_enabled = 1
        """
    )


async def was_sent(user_id: int, work_id: int | None, key: str) -> bool:
    if work_id is None:
        row = await fetchone("SELECT id FROM sent_reminders WHERE user_id = ? AND work_id IS NULL AND reminder_key = ?", (user_id, key))
    else:
        row = await fetchone("SELECT id FROM sent_reminders WHERE user_id = ? AND work_id = ? AND reminder_key = ?", (user_id, work_id, key))
    return row is not None


async def mark_sent(user_id: int, work_id: int | None, key: str) -> None:
    try:
        await execute("INSERT INTO sent_reminders (user_id, work_id, reminder_key, sent_at) VALUES (?, ?, ?, ?)", (user_id, work_id, key, utc_now()))
    except Exception:
        pass


async def works_due_on(user_id: int, target_date: date) -> list[aiosqlite.Row]:
    return await fetchall(
        """
        SELECT w.*, s.title AS subject_title
        FROM works w JOIN subjects s ON s.id = w.subject_id
        WHERE w.user_id = ? AND w.status = 'active' AND w.deadline_date = ?
        ORDER BY s.title, w.work_type, w.title
        """,
        (user_id, target_date.isoformat()),
    )


async def works_between(user_id: int, start: date, end: date) -> list[aiosqlite.Row]:
    return await fetchall(
        """
        SELECT w.*, s.title AS subject_title
        FROM works w JOIN subjects s ON s.id = w.subject_id
        WHERE w.user_id = ? AND w.status = 'active' AND w.deadline_date BETWEEN ? AND ?
        ORDER BY w.deadline_date, s.title, w.work_type, w.title
        """,
        (user_id, start.isoformat(), end.isoformat()),
    )


def format_works_list(rows: list[aiosqlite.Row], today: date) -> str:
    if not rows:
        return "Работ не найдено."
    lines = []
    for idx, w in enumerate(rows, 1):
        status = work_status_label(w, today)
        lines.append(
            f"{idx}. {w['subject_title']} — {emphasize_type(w['work_type'])}: {w['title']}\n"
            f"   Дата сдачи: {format_ru_date(w['deadline_date'])}\n"
            f"   Статус: {status}"
        )
    return "\n\n".join(lines)


def format_work_card(work: aiosqlite.Row, today: date) -> str:
    return (
        f"📌 {work['subject_title']}\n\n"
        f"{emphasize_type(work['work_type'])}: {work['title']}\n"
        f"Дата сдачи: {format_ru_date(work['deadline_date'])}\n"
        f"Статус: {work_status_label(work, today)}"
    )


def format_digest(title: str, rows: list[aiosqlite.Row], empty_text: str) -> str:
    if not rows:
        return empty_text
    grouped = defaultdict(list)
    for w in rows:
        grouped[w["subject_title"]].append(w)

    lines = [title, ""]
    for subject, items in grouped.items():
        lines.append(f"{subject}:")
        for w in items:
            lines.append(f"— {emphasize_type(w['work_type'])}: {w['title']} — {format_ru_date(w['deadline_date'])}")
        lines.append("")
    return "\n".join(lines).strip()
