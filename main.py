from __future__ import annotations

import asyncio
import logging
from datetime import date
from urllib.parse import unquote

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

import keyboards as kb
import services as svc
from config import get_settings
from db import init_db
from scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()
router = Router()


class SubjectStates(StatesGroup):
    add_title = State()
    rename_title = State()


class WorkStates(StatesGroup):
    title = State()
    deadline = State()
    rename = State()
    reschedule = State()


class ProfileStates(StatesGroup):
    university = State()


async def get_app_user(message_or_callback) -> dict:
    tg_user = message_or_callback.from_user
    return await svc.ensure_user(tg_user)


async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


async def show_main(target, state: FSMContext | None = None) -> None:
    if state:
        await state.clear()
    text = "🏠 Главное меню\n\nВыберите раздел:"
    if isinstance(target, CallbackQuery):
        await target.answer()
        await safe_edit(target, text, kb.main_menu_kb())
    else:
        await target.answer(text, reply_markup=kb.main_menu_kb())


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await get_app_user(message)
    await show_main(message, state)


@router.callback_query(F.data == "main")
async def cb_main(callback: CallbackQuery, state: FSMContext):
    await get_app_user(callback)
    await show_main(callback, state)


@router.callback_query(F.data == "subjects")
async def cb_subjects(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user = await get_app_user(callback)
    subjects = await svc.get_subjects(user["id"])
    text = "📚 Список предметов\n\n"
    text += "Выберите предмет или добавьте новый." if subjects else "У вас пока нет предметов. Добавьте первый предмет."
    await safe_edit(callback, text, kb.subjects_kb(subjects))


@router.callback_query(F.data == "subject_add")
async def cb_subject_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SubjectStates.add_title)
    await safe_edit(callback, "Введите название предмета:", kb.home_back_kb("subjects"))


@router.message(SubjectStates.add_title)
async def msg_subject_add(message: Message, state: FSMContext):
    user = await get_app_user(message)
    ok, text = await svc.create_subject(user["id"], message.text or "")
    await state.clear()
    await message.answer(text, reply_markup=kb.after_subject_added_kb() if ok else kb.home_back_kb("subjects"))


@router.callback_query(F.data.startswith("subject:"))
async def cb_subject_card(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user = await get_app_user(callback)
    subject_id = int(callback.data.split(":")[1])
    subject = await svc.get_subject(user["id"], subject_id)
    if not subject:
        await safe_edit(callback, "Предмет не найден.", kb.home_back_kb("subjects"))
        return
    today = svc.today_in_tz(user["timezone"])
    counts = await svc.count_subject_works(user["id"], subject_id, today)
    text = (
        f"📚 Предмет: {subject['title']}\n\n"
        f"Всего работ: {counts['total']}\n"
        f"Активных: {counts['active']}\n"
        f"Выполнено: {counts['completed']}\n"
        f"Просрочено: {counts['overdue']}"
    )
    await safe_edit(callback, text, kb.subject_card_kb(subject_id))


@router.callback_query(F.data.startswith("subject_rename:"))
async def cb_subject_rename(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    subject_id = int(callback.data.split(":")[1])
    await state.set_state(SubjectStates.rename_title)
    await state.update_data(subject_id=subject_id)
    await safe_edit(callback, "Введите новое название предмета:", kb.home_back_kb(f"subject:{subject_id}"))


@router.message(SubjectStates.rename_title)
async def msg_subject_rename(message: Message, state: FSMContext):
    user = await get_app_user(message)
    data = await state.get_data()
    subject_id = int(data["subject_id"])
    ok, text = await svc.rename_subject(user["id"], subject_id, message.text or "")
    await state.clear()
    await message.answer(text, reply_markup=kb.home_back_kb(f"subject:{subject_id}"))


@router.callback_query(F.data.startswith("subject_delete_confirm:"))
async def cb_subject_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    subject_id = int(callback.data.split(":")[1])
    subject = await svc.get_subject(user["id"], subject_id)
    if not subject:
        await safe_edit(callback, "Предмет не найден.", kb.home_back_kb("subjects"))
        return
    await safe_edit(
        callback,
        f"Удалить предмет «{subject['title']}»?\n\nБудут удалены все работы этого предмета.",
        kb.confirm_subject_delete_kb(subject_id),
    )


@router.callback_query(F.data.startswith("subject_delete:"))
async def cb_subject_delete(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    subject_id = int(callback.data.split(":")[1])
    await svc.delete_subject(user["id"], subject_id)
    await safe_edit(callback, "Предмет удален вместе со всеми его работами.", kb.after_subject_added_kb())


@router.callback_query(F.data.startswith("work_add:"))
async def cb_work_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    subject_id = int(callback.data.split(":")[1])
    await safe_edit(callback, "Выберите тип работы:", kb.work_types_kb(subject_id))


@router.callback_query(F.data.startswith("work_type:"))
async def cb_work_type(callback: CallbackQuery):
    await callback.answer()
    _, subject_id, type_code = callback.data.split(":")
    work_type = svc.WORK_TYPE_CODES[type_code]
    await safe_edit(callback, f"Сколько работ типа «{work_type}» хотите добавить?", kb.work_count_kb(int(subject_id), type_code))


@router.callback_query(F.data.startswith("work_count:"))
async def cb_work_count(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, subject_id, type_code, count = callback.data.split(":")
    await state.set_state(WorkStates.title)
    await state.update_data(
        subject_id=int(subject_id),
        type_code=type_code,
        work_type=svc.WORK_TYPE_CODES[type_code],
        count=int(count),
        current=1,
        items=[],
    )
    await safe_edit(callback, f"Введите название работы 1 из {count}:", kb.home_back_kb(f"work_add:{subject_id}"))


@router.message(WorkStates.title)
async def msg_work_title(message: Message, state: FSMContext):
    title = " ".join((message.text or "").strip().split())
    if not (2 <= len(title) <= 128):
        await message.answer("Название работы должно быть от 2 до 128 символов. Попробуйте еще раз.")
        return
    data = await state.get_data()
    await state.update_data(current_title=title)
    await state.set_state(WorkStates.deadline)
    await message.answer(
        f"Введите дату сдачи для «{title}».\n\nФормат: ДД.ММ.ГГГГ, например 15.05.2026",
        reply_markup=kb.home_back_kb(f"work_add:{data['subject_id']}"),
    )


@router.message(WorkStates.deadline)
async def msg_work_deadline(message: Message, state: FSMContext):
    user = await get_app_user(message)
    parsed = svc.parse_ru_date(message.text or "")
    if not parsed:
        await message.answer("Не удалось распознать дату. Введите дату в формате ДД.ММ.ГГГГ, например 15.05.2026.")
        return
    today = svc.today_in_tz(user["timezone"])
    if parsed < today:
        await state.update_data(pending_deadline=parsed.isoformat())
        await message.answer(
            "Эта дата уже прошла. Работа сразу попадет в просроченные.\n\nДобавить работу с просроченным дедлайном?",
            reply_markup=kb.past_date_confirm_kb(),
        )
        return
    await finish_one_work(message, state, parsed)


@router.callback_query(F.data == "past_date_retry")
async def cb_past_retry(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(WorkStates.deadline)
    await safe_edit(callback, "Введите другую дату в формате ДД.ММ.ГГГГ:")


@router.callback_query(F.data == "past_date_yes")
async def cb_past_yes(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    parsed = date.fromisoformat(data["pending_deadline"])
    await finish_one_work(callback.message, state, parsed)


async def finish_one_work(message: Message, state: FSMContext, deadline: date):
    user = await get_app_user(message)
    data = await state.get_data()
    items = list(data.get("items", []))
    items.append({"title": data["current_title"], "deadline_date": deadline.isoformat()})
    current = int(data["current"])
    count = int(data["count"])
    subject_id = int(data["subject_id"])

    if current < count:
        await state.update_data(items=items, current=current + 1, current_title=None, pending_deadline=None)
        await state.set_state(WorkStates.title)
        await message.answer(f"Введите название работы {current + 1} из {count}:", reply_markup=kb.home_back_kb(f"work_add:{subject_id}"))
        return

    await svc.create_works(user["id"], subject_id, data["work_type"], items)
    await state.clear()
    await message.answer("Работы добавлены.", reply_markup=kb.after_works_added_kb(subject_id))


@router.callback_query(F.data == "works")
async def cb_works(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await safe_edit(callback, "📝 Список работ\n\nВыберите способ просмотра:", kb.works_menu_kb())


@router.callback_query(F.data == "works_status_menu")
async def cb_status_menu(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback, "📌 Работы по статусу:", kb.status_menu_kb())


@router.callback_query(F.data == "works_type_menu")
async def cb_type_menu(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback, "🧪 Работы по типу:", kb.type_menu_kb())


@router.callback_query(F.data.startswith("subworks:"))
async def cb_subject_works(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    _, subject_id_s, page_s = callback.data.split(":")
    subject_id, page = int(subject_id_s), int(page_s)
    today = svc.today_in_tz(user["timezone"])
    subject = await svc.get_subject(user["id"], subject_id)
    if not subject:
        await safe_edit(callback, "Предмет не найден.", kb.home_back_kb("subjects"))
        return
    rows, total = await svc.list_subject_works(user["id"], subject_id, page, today)
    text = f"📝 Работы предмета «{subject['title']}»\nСтраница {page + 1}\n\n{svc.format_works_list(rows, today)}"
    source = f"subworks:{subject_id}:{page}"
    await safe_edit(callback, text, kb.works_list_kb(rows, total, page, source, f"subject:{subject_id}"))


@router.callback_query(F.data.startswith("status:"))
async def cb_status_list(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    _, status_key, page_s = callback.data.split(":")
    page = int(page_s)
    today = svc.today_in_tz(user["timezone"])
    rows, total = await svc.list_by_status(user["id"], status_key, page, today)
    title_map = {"overdue": "❗ Просроченные", "upcoming": "⏰ Предстоящие", "completed": "✅ Выполненные"}
    text = f"{title_map.get(status_key, 'Работы')}\nСтраница {page + 1}\n\n{svc.format_works_list(rows, today)}"
    source = f"status:{status_key}:{page}"
    await safe_edit(callback, text, kb.works_list_kb(rows, total, page, source, "works_status_menu"))


@router.callback_query(F.data.startswith("type:"))
async def cb_type_list(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    _, type_code, page_s = callback.data.split(":")
    page = int(page_s)
    work_type = svc.WORK_TYPE_CODES[type_code]
    today = svc.today_in_tz(user["timezone"])
    rows, total = await svc.list_by_type(user["id"], work_type, page, today)
    text = f"🧪 {work_type}\nСтраница {page + 1}\n\n{svc.format_works_list(rows, today)}"
    source = f"type:{type_code}:{page}"
    await safe_edit(callback, text, kb.works_list_kb(rows, total, page, source, "works_type_menu"))


@router.callback_query(F.data == "quick")
async def cb_quick_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await safe_edit(callback, "⚡ Быстрый просмотр\n\nВыберите период:", kb.quick_menu_kb())


@router.callback_query(F.data.startswith("quick:"))
async def cb_quick_list(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    _, mode, page_s = callback.data.split(":")
    page = int(page_s)
    today = svc.today_in_tz(user["timezone"])
    rows, total, title = await svc.quick_view(user["id"], mode, page, today)
    text = f"⚡ {title}\nСтраница {page + 1}\n\n{svc.format_works_list(rows, today)}"
    source = f"quick:{mode}:{page}"
    await safe_edit(callback, text, kb.works_list_kb(rows, total, page, source, "quick"))


@router.callback_query(F.data.startswith("work:"))
async def cb_work_card(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user = await get_app_user(callback)
    _, work_id_s, source_q = callback.data.split(":", 2)
    work_id, source = int(work_id_s), unquote(source_q)
    work = await svc.get_work(user["id"], work_id)
    if not work:
        await safe_edit(callback, "Работа не найдена.", kb.home_back_kb("works"))
        return
    today = svc.today_in_tz(user["timezone"])
    await safe_edit(callback, svc.format_work_card(work, today), kb.work_card_kb(work_id, source, work["status"] == "completed"))


@router.callback_query(F.data.startswith("work_done:"))
async def cb_work_done(callback: CallbackQuery):
    await callback.answer("Готово")
    user = await get_app_user(callback)
    _, work_id_s, source_q = callback.data.split(":", 2)
    work_id, source = int(work_id_s), unquote(source_q)
    await svc.set_work_completed(user["id"], work_id)
    work = await svc.get_work(user["id"], work_id)
    today = svc.today_in_tz(user["timezone"])
    await safe_edit(callback, svc.format_work_card(work, today), kb.work_card_kb(work_id, source, True))


@router.callback_query(F.data.startswith("work_active:"))
async def cb_work_active(callback: CallbackQuery):
    await callback.answer("Возвращено в активные")
    user = await get_app_user(callback)
    _, work_id_s, source_q = callback.data.split(":", 2)
    work_id, source = int(work_id_s), unquote(source_q)
    await svc.set_work_active(user["id"], work_id)
    work = await svc.get_work(user["id"], work_id)
    today = svc.today_in_tz(user["timezone"])
    await safe_edit(callback, svc.format_work_card(work, today), kb.work_card_kb(work_id, source, False))


@router.callback_query(F.data.startswith("work_delete_confirm:"))
async def cb_work_delete_confirm(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    _, work_id_s, source_q = callback.data.split(":", 2)
    work_id, source = int(work_id_s), unquote(source_q)
    work = await svc.get_work(user["id"], work_id)
    if not work:
        await safe_edit(callback, "Работа не найдена.", kb.home_back_kb("works"))
        return
    await safe_edit(callback, f"Удалить работу «{work['title']}»?", kb.confirm_work_delete_kb(work_id, source))


@router.callback_query(F.data.startswith("work_delete:"))
async def cb_work_delete(callback: CallbackQuery):
    await callback.answer("Удалено")
    user = await get_app_user(callback)
    _, work_id_s, source_q = callback.data.split(":", 2)
    source = unquote(source_q)
    await svc.delete_work(user["id"], int(work_id_s))
    await safe_edit(callback, "Работа удалена.", kb.home_back_kb(kb.source_with_page(source, int(source.split(':')[-1]) if source.split(':')[-1].isdigit() else 0)))


@router.callback_query(F.data.startswith("work_rename:"))
async def cb_work_rename(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, work_id_s, source_q = callback.data.split(":", 2)
    await state.set_state(WorkStates.rename)
    await state.update_data(work_id=int(work_id_s), source=unquote(source_q))
    await safe_edit(callback, "Введите новое название работы:")


@router.message(WorkStates.rename)
async def msg_work_rename(message: Message, state: FSMContext):
    user = await get_app_user(message)
    data = await state.get_data()
    ok, text = await svc.rename_work(user["id"], int(data["work_id"]), message.text or "")
    await state.clear()
    await message.answer(text, reply_markup=kb.home_back_kb(f"work:{data['work_id']}:{data['source']}" if ok else data["source"]))


@router.callback_query(F.data.startswith("work_reschedule:"))
async def cb_work_reschedule(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    _, work_id_s, source_q = callback.data.split(":", 2)
    await state.set_state(WorkStates.reschedule)
    await state.update_data(work_id=int(work_id_s), source=unquote(source_q))
    await safe_edit(callback, "Введите новую дату сдачи в формате ДД.ММ.ГГГГ:")


@router.message(WorkStates.reschedule)
async def msg_work_reschedule(message: Message, state: FSMContext):
    user = await get_app_user(message)
    parsed = svc.parse_ru_date(message.text or "")
    if not parsed:
        await message.answer("Не удалось распознать дату. Введите дату в формате ДД.ММ.ГГГГ, например 15.05.2026.")
        return
    data = await state.get_data()
    await svc.reschedule_work(user["id"], int(data["work_id"]), parsed)
    await state.clear()
    await message.answer("Дедлайн перенесен.", reply_markup=kb.home_back_kb(f"work:{data['work_id']}:{data['source']}"))


@router.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user = await get_app_user(callback)
    today = svc.today_in_tz(user["timezone"])
    st = await svc.get_statistics(user["id"], today)
    percent = f"{st['completion_percent']}%" if st["completion_percent"] is not None else "пока недостаточно данных"
    lines = [
        "📊 Статистика",
        "",
        f"Всего работ: {st['total']}",
        f"Выполнено: {st['completed']}",
        f"Активных: {st['active']}",
        f"Просрочено: {st['overdue']}",
        f"Общий процент выполнения: {percent}",
        "",
        "По типам:",
    ]
    for wt in svc.WORK_TYPES:
        lines.append(f"— {wt}: {st['by_type'].get(wt, 0)}")
    lines.append("")
    lines.append("По предметам:")
    if st["by_subject"]:
        for subject, data in st["by_subject"].items():
            p = f"{data['percent']}%" if data["percent"] is not None else "недостаточно данных"
            lines.append(f"— {subject}: {p}")
    else:
        lines.append("Пока нет данных.")
    await safe_edit(callback, "\n".join(lines), kb.home_back_kb("main"))


@router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user = await get_app_user(callback)
    tz_label = svc.TIMEZONE_TO_LABEL.get(user["timezone"], user["timezone"])
    text = (
        "👤 Профиль\n\n"
        f"Telegram ID: {user['telegram_id']}\n"
        f"Username: @{user['username']}\n" if user["username"] else "👤 Профиль\n\n"
    )
    if not user["username"]:
        text += f"Telegram ID: {user['telegram_id']}\nUsername: не указан\n"
    text += f"ВУЗ: {user['university'] or 'не указан'}\nЧасовой пояс: {tz_label}"
    await safe_edit(callback, text, kb.profile_kb())


@router.callback_query(F.data == "profile_university")
async def cb_profile_university(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ProfileStates.university)
    await safe_edit(callback, "Введите название вашего ВУЗа:", kb.home_back_kb("profile"))


@router.message(ProfileStates.university)
async def msg_university(message: Message, state: FSMContext):
    user = await get_app_user(message)
    university = " ".join((message.text or "").strip().split())
    if not (2 <= len(university) <= 128):
        await message.answer("Название ВУЗа должно быть от 2 до 128 символов.")
        return
    await svc.update_university(user["id"], university)
    await state.clear()
    await message.answer(f"ВУЗ сохранен: {university}", reply_markup=kb.home_back_kb("profile"))


@router.callback_query(F.data == "profile_timezone")
async def cb_profile_timezone(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback, "🌍 Выберите часовой пояс:", kb.timezone_kb())


@router.callback_query(F.data.startswith("tz:"))
async def cb_timezone_set(callback: CallbackQuery):
    await callback.answer("Часовой пояс сохранен")
    user = await get_app_user(callback)
    tz = callback.data.split(":", 1)[1]
    await svc.update_timezone(user["id"], tz)
    await safe_edit(callback, f"Часовой пояс сохранен: {svc.TIMEZONE_TO_LABEL.get(tz, tz)}", kb.home_back_kb("profile"))


@router.callback_query(F.data == "reminders")
async def cb_reminders(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await safe_edit(callback, "🔔 Умные напоминания\n\nВыберите раздел:", kb.reminders_menu_kb())


@router.callback_query(F.data == "reminder_templates")
async def cb_templates(callback: CallbackQuery):
    await callback.answer()
    lines = ["📋 Шаблоны напоминаний", ""]
    for wt, days in svc.REMINDER_TEMPLATES.items():
        lines.append(f"{svc.emphasize_type(wt)}: за {', за '.join(str(d) for d in days)} дн.")
    lines.append("")
    lines.append("За 1 день предупреждаем по любой работе. Экзамены и контрольные в дайджестах выделяются жирным.")
    await safe_edit(callback, "\n".join(lines), kb.home_back_kb("reminders"))


@router.callback_query(F.data == "reminder_system")
async def cb_reminder_system(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    rs = await svc.get_reminder_settings(user["id"])
    status = "включена" if rs["is_enabled"] else "выключена"
    freq = "в начале недели" if rs["frequency"] == "weekly" else "в начале месяца"
    text = f"⚙️ Система напоминаний\n\nСтатус: {status}\nВремя дайджеста: {rs['digest_time']}\nЧастота: {freq}"
    await safe_edit(callback, text, kb.reminder_system_kb(bool(rs["is_enabled"])))


@router.callback_query(F.data == "reminder_toggle")
async def cb_reminder_toggle(callback: CallbackQuery):
    await callback.answer()
    user = await get_app_user(callback)
    await svc.toggle_reminders(user["id"])
    await cb_reminder_system(callback)


@router.callback_query(F.data == "reminder_time")
async def cb_reminder_time(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback, "🕘 Выберите время дайджеста:", kb.time_grid_kb())


@router.callback_query(F.data.startswith("digest_time:"))
async def cb_digest_time(callback: CallbackQuery):
    await callback.answer("Время сохранено")
    user = await get_app_user(callback)
    hour = int(callback.data.split(":")[1])
    await svc.set_digest_time(user["id"], hour)
    await safe_edit(callback, f"Время дайджеста сохранено: {hour:02d}:00", kb.home_back_kb("reminder_system"))


@router.callback_query(F.data == "reminder_frequency")
async def cb_frequency_menu(callback: CallbackQuery):
    await callback.answer()
    await safe_edit(callback, "📆 Выберите частоту напоминаний:", kb.frequency_kb())


@router.callback_query(F.data.startswith("freq:"))
async def cb_frequency_set(callback: CallbackQuery):
    await callback.answer("Частота сохранена")
    user = await get_app_user(callback)
    frequency = callback.data.split(":")[1]
    await svc.set_frequency(user["id"], frequency)
    label = "в начале каждой недели" if frequency == "weekly" else "в начале каждого месяца"
    await safe_edit(callback, f"Частота сохранена: {label}", kb.home_back_kb("reminder_system"))


@router.message()
async def fallback(message: Message):
    await message.answer("Я не понял это сообщение. Используйте меню или команду /start.", reply_markup=kb.main_menu_kb())


async def main() -> None:
    await init_db()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    start_scheduler(bot)
    logger.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
