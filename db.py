from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import aiosqlite

from config import get_settings

settings = get_settings()


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


async def connect() -> aiosqlite.Connection:
    db_path = settings.database_path
    db_dir = os.path.dirname(db_path)
    if db_dir and db_dir not in (".", ""):
        os.makedirs(db_dir, exist_ok=True)

    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON;")
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA busy_timeout=5000;")
    return db


async def init_db() -> None:
    async with await connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                university TEXT,
                timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, title)
            );

            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                work_type TEXT NOT NULL,
                title TEXT NOT NULL,
                deadline_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reminder_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                digest_time TEXT NOT NULL DEFAULT '09:00',
                frequency TEXT NOT NULL DEFAULT 'weekly',
                daily_tomorrow_digest_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sent_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_id INTEGER,
                reminder_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                UNIQUE(user_id, work_id, reminder_key)
            );

            CREATE INDEX IF NOT EXISTS idx_subjects_user_id ON subjects(user_id);
            CREATE INDEX IF NOT EXISTS idx_works_user_id ON works(user_id);
            CREATE INDEX IF NOT EXISTS idx_works_subject_id ON works(subject_id);
            CREATE INDEX IF NOT EXISTS idx_works_deadline ON works(deadline_date);
            CREATE INDEX IF NOT EXISTS idx_works_status ON works(status);
            """
        )
        await db.commit()


async def fetchone(query: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
    async with await connect() as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchone()


async def fetchall(query: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
    async with await connect() as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()


async def execute(query: str, params: tuple[Any, ...] = ()) -> int:
    async with await connect() as db:
        cursor = await db.execute(query, params)
        await db.commit()
        return int(cursor.lastrowid or 0)


async def execute_many(query: str, seq_of_params: list[tuple[Any, ...]]) -> None:
    async with await connect() as db:
        await db.executemany(query, seq_of_params)
        await db.commit()
