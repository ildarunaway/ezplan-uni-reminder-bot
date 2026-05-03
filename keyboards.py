from __future__ import annotations

from math import ceil
from urllib.parse import quote

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services import PAGE_SIZE, RUSSIAN_TIMEZONES, WORK_TYPE_CODES, WORK_TYPE_TO_CODE


def main_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📚 Список предметов", callback_data="subjects")
    b.button(text="📝 Список работ", callback_data="works")
    b.button(text="📊 Статистика", callback_data="stats")
    b.button(text="👤 Профиль", callback_data="profile")
    b.button(text="🔔 Умные напоминания", callback_data="reminders")
    b.button(text="⚡ Быстрый просмотр", callback_data="quick")
    b.adjust(1)
    return b.as_markup()


def home_back_kb(back: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if back:
        b.button(text="⬅️ Назад", callback_data=back)
    b.button(text="🏠 Главное меню", callback_data="main")
    b.adjust(1)
    return b.as_markup()


def subjects_kb(subjects) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in subjects:
        b.button(text=s["title"], callback_data=f"subject:{s['id']}")
    b.button(text="➕ Добавить предмет", callback_data="subject_add")
    b.button(text="🏠 Главное меню", callback_data="main")
    b.adjust(1)
    return b.as_markup()


def subject_card_kb(subject_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить работу", callback_data=f"work_add:{subject_id}")
    b.button(text="📝 Редактировать список", callback_data=f"subworks:{subject_id}:0")
    b.button(text="✏️ Переименовать предмет", callback_data=f"subject_rename:{subject_id}")
    b.button(text="🗑 Удалить предмет", callback_data=f"subject_delete_confirm:{subject_id}")
    b.button(text="⬅️ Назад", callback_data="subjects")
    b.adjust(1)
    return b.as_markup()


def confirm_subject_delete_kb(subject_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, удалить", callback_data=f"subject_delete:{subject_id}")
    b.button(text="❌ Отмена", callback_data=f"subject:{subject_id}")
    b.adjust(1)
    return b.as_markup()


def work_types_kb(subject_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, title in WORK_TYPE_CODES.items():
        b.button(text=title, callback_data=f"work_type:{subject_id}:{code}")
    b.button(text="⬅️ Назад", callback_data=f"subject:{subject_id}")
    b.adjust(1)
    return b.as_markup()


def work_count_kb(subject_id: int, type_code: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i in range(1, 6):
        b.button(text=str(i), callback_data=f"work_count:{subject_id}:{type_code}:{i}")
    b.button(text="⬅️ Назад", callback_data=f"work_add:{subject_id}")
    b.adjust(5, 1)
    return b.as_markup()


def past_date_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, добавить", callback_data="past_date_yes")
    b.button(text="✏️ Ввести другую дату", callback_data="past_date_retry")
    b.adjust(1)
    return b.as_markup()


def after_subject_added_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📚 К списку предметов", callback_data="subjects")
    b.button(text="🏠 Главное меню", callback_data="main")
    b.adjust(1)
    return b.as_markup()


def after_works_added_kb(subject_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 К работам предмета", callback_data=f"subworks:{subject_id}:0")
    b.button(text="🏠 Главное меню", callback_data="main")
    b.adjust(1)
    return b.as_markup()


def works_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📌 По статусу выполнения", callback_data="works_status_menu")
    b.button(text="🧪 По типу работы", callback_data="works_type_menu")
    b.button(text="🏠 Главное меню", callback_data="main")
    b.adjust(1)
    return b.as_markup()


def status_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❗ Просроченные", callback_data="status:overdue:0")
    b.button(text="⏰ Предстоящие", callback_data="status:upcoming:0")
    b.button(text="✅ Выполненные", callback_data="status:completed:0")
    b.button(text="⬅️ Назад", callback_data="works")
    b.adjust(1)
    return b.as_markup()


def type_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, title in WORK_TYPE_CODES.items():
        b.button(text=title, callback_data=f"type:{code}:0")
    b.button(text="⬅️ Назад", callback_data="works")
    b.adjust(1)
    return b.as_markup()


def quick_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Сегодня", callback_data="quick:today:0")
    b.button(text="Завтра", callback_data="quick:tomorrow:0")
    b.button(text="На 7 дней", callback_data="quick:7:0")
    b.button(text="На 30 дней", callback_data="quick:30:0")
    b.button(text="Просроченные", callback_data="quick:overdue:0")
    b.button(text="🏠 Главное меню", callback_data="main")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def works_list_kb(rows, total: int, page: int, source: str, back_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for w in rows:
        title = f"{w['subject_title']} · {w['title']}"
        if len(title) > 58:
            title = title[:55] + "..."
        b.button(text=title, callback_data=f"work:{w['id']}:{quote(source, safe='')}")

    pages = max(1, ceil(total / PAGE_SIZE))
    nav = []
    if page > 0:
        nav.append(("⬅️ Предыдущая", source_with_page(source, page - 1)))
    if page + 1 < pages:
        nav.append(("➡️ Следующая", source_with_page(source, page + 1)))
    for text, cb in nav:
        b.button(text=text, callback_data=cb)

    b.button(text="⬅️ Назад", callback_data=back_cb)
    b.adjust(1)
    return b.as_markup()


def source_with_page(source: str, page: int) -> str:
    parts = source.split(":")
    if parts[0] == "subworks":
        return f"subworks:{parts[1]}:{page}"
    if parts[0] == "status":
        return f"status:{parts[1]}:{page}"
    if parts[0] == "type":
        return f"type:{parts[1]}:{page}"
    if parts[0] == "quick":
        return f"quick:{parts[1]}:{page}"
    return "works"


def work_card_kb(work_id: int, source: str, is_completed: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if is_completed:
        b.button(text="↩️ Вернуть в активные", callback_data=f"work_active:{work_id}:{quote(source, safe='')}")
    else:
        b.button(text="✅ Отметить выполненной", callback_data=f"work_done:{work_id}:{quote(source, safe='')}")
    b.button(text="📅 Перенести дедлайн", callback_data=f"work_reschedule:{work_id}:{quote(source, safe='')}")
    b.button(text="✏️ Изменить название", callback_data=f"work_rename:{work_id}:{quote(source, safe='')}")
    b.button(text="🗑 Удалить", callback_data=f"work_delete_confirm:{work_id}:{quote(source, safe='')}")
    b.button(text="⬅️ Назад", callback_data=source_with_page(source, int(source.split(':')[-1]) if source.split(':')[-1].isdigit() else 0))
    b.adjust(1)
    return b.as_markup()


def confirm_work_delete_kb(work_id: int, source: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, удалить", callback_data=f"work_delete:{work_id}:{quote(source, safe='')}")
    b.button(text="❌ Отмена", callback_data=f"work:{work_id}:{quote(source, safe='')}")
    b.adjust(1)
    return b.as_markup()


def profile_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🏫 Указать ВУЗ", callback_data="profile_university")
    b.button(text="🌍 Выбрать часовой пояс", callback_data="profile_timezone")
    b.button(text="🏠 Главное меню", callback_data="main")
    b.adjust(1)
    return b.as_markup()


def timezone_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, tz in RUSSIAN_TIMEZONES.items():
        b.button(text=label, callback_data=f"tz:{tz}")
    b.button(text="⬅️ Назад", callback_data="profile")
    b.adjust(1)
    return b.as_markup()


def reminders_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 Шаблоны напоминаний", callback_data="reminder_templates")
    b.button(text="⚙️ Система напоминаний", callback_data="reminder_system")
    b.button(text="🏠 Главное меню", callback_data="main")
    b.adjust(1)
    return b.as_markup()


def reminder_system_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔘 Выключить" if is_enabled else "🟢 Включить", callback_data="reminder_toggle")
    b.button(text="🕘 Изменить время дайджеста", callback_data="reminder_time")
    b.button(text="📆 Частота напоминаний", callback_data="reminder_frequency")
    b.button(text="⬅️ Назад", callback_data="reminders")
    b.adjust(1)
    return b.as_markup()


def time_grid_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for hour in range(24):
        b.button(text=f"{hour:02d}:00", callback_data=f"digest_time:{hour}")
    b.button(text="⬅️ Назад", callback_data="reminder_system")
    b.adjust(4, 4, 4, 4, 4, 4, 1)
    return b.as_markup()


def frequency_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="В начале каждой недели", callback_data="freq:weekly")
    b.button(text="В начале каждого месяца", callback_data="freq:monthly")
    b.button(text="⬅️ Назад", callback_data="reminder_system")
    b.adjust(1)
    return b.as_markup()
