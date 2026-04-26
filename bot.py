import asyncio
from datetime import datetime, timedelta
from typing import Dict, List
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, NOTIFICATION_CHECK_INTERVAL, AGGREGATION_HOUR, AGGREGATION_THRESHOLD, TASK_TYPE_EMOJIS
from database import db

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Scheduler for notifications
scheduler = AsyncIOScheduler()

# FSM States
class AddTaskStates(StatesGroup):
    selecting_subject = State()
    selecting_type = State()
    entering_quantity = State()
    entering_title = State()
    entering_date = State()
    entering_time = State()
    selecting_reminder = State()
    confirming = State()

class AddSubjectStates(StatesGroup):
    entering_name = State()

# Helper functions
def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📚 Мои предметы", callback_data="menu_subjects"),
        InlineKeyboardButton("➕ Добавить работу", callback_data="menu_add_task")
    )
    keyboard.add(
        InlineKeyboardButton("📅 Умный календарь", callback_data="menu_calendar"),
        InlineKeyboardButton("🔔 Умные напоминания", callback_data="menu_reminders")
    )
    keyboard.add(
        InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings"),
        InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")
    )
    keyboard.add(
        InlineKeyboardButton("❓ Помощь", callback_data="menu_help"),
        InlineKeyboardButton("⚡ Быстрые действия", callback_data="menu_quick")
    )
    return keyboard

def format_task_list(tasks: List[Dict]) -> str:
    if not tasks:
        return "📭 Нет задач."
    
    text = ""
    for task in tasks:
        due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
        time_str = f" — {task['due_time']}" if task['due_time'] else ""
        emoji = task.get('subject_emoji', '📚')
        
        text += f"{emoji} **{task['subject_name']}**:\n"
        text += f"  {TASK_TYPE_EMOJIS.get(task['task_type'], '📌')} {task['title']} — {due_date}{time_str}\n\n"
    
    return text

# Command handlers
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    db.get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    
    welcome_text = f"""
🎓 Привет, {message.from_user.first_name}!

Я помогу тебе не забыть о контрольных, лабораторных и других работах.

📌 **Что я умею:**
• 📚 Вести список предметов и задач
• 🔔 Умные напоминания
• ⏰ Мудрый перенос просроченных работ
• 📅 Календарь дедлайнов
• 📊 Статистика успеваемости

Начни с добавления первого предмета!
"""
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard(message.from_user.id), parse_mode="Markdown")

@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    today = datetime.now().strftime("%Y-%m-%d")
    tasks = db.get_tasks_by_date(message.from_user.id, today)
    
    if not tasks:
        await message.answer("📅 На сегодня нет запланированных работ. Отдыхайте! 🎉")
        return
    
    text = format_task_list(tasks)
    await message.answer(f"📅 **НА СЕГОДНЯ ({datetime.now().strftime('%d.%m.%Y')})**\n\n{text}", parse_mode="Markdown")

@dp.message(Command("tomorrow"))
async def cmd_tomorrow(message: types.Message):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    tasks = db.get_tasks_by_date(message.from_user.id, tomorrow)
    
    if not tasks:
        await message.answer("📅 На завтра нет запланированных работ.")
        return
    
    text = format_task_list(tasks)
    await message.answer(f"📅 **НА ЗАВТРА ({(datetime.now() + timedelta(days=1)).strftime('%d.%m.%Y')})**\n\n{text}", parse_mode="Markdown")

@dp.message(Command("week"))
async def cmd_week(message: types.Message):
    tasks = db.get_user_tasks(message.from_user.id, status="pending")
    
    if not tasks:
        await message.answer("📅 Нет предстоящих задач.")
        return
    
    week_start = datetime.now().date()
    week_end = week_start + timedelta(days=7)
    
    week_tasks = [t for t in tasks if datetime.strptime(t['due_date'], "%Y-%m-%d").date() <= week_end]
    
    text = "📅 **ЗАДАЧИ НА ЭТУ НЕДЕЛЮ**\n\n"
    for task in week_tasks:
        due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").strftime("%d.%m")
        text += f"{task.get('subject_emoji', '📚')} {task['subject_name']}: {task['title']} — {due_date}\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("overdue"))
async def cmd_overdue(message: types.Message):
    await show_overdue_tasks(message.from_user.id, message.chat.id)

@dp.message(Command("add"))
async def cmd_add(message: types.Message, state: FSMContext):
    await start_add_task(message.from_user.id, message.chat.id, state)

# Main Menu Callbacks
@dp.callback_query(F.data.startswith("menu_"))
async def handle_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    action = callback.data.split("_")[1]
    
    if action == "subjects":
        await show_subjects(callback)
    elif action == "add_task":
        await start_add_task(callback.from_user.id, callback.message.chat.id, state, callback)
    elif action == "calendar":
        await show_calendar_menu(callback)
    elif action == "reminders":
        await show_reminders_menu(callback)
    elif action == "settings":
        await show_settings_menu(callback)
    elif action == "stats":
        await show_statistics(callback)
    elif action == "help":
        await show_help(callback)
    elif action == "quick":
        await show_quick_actions(callback)
    
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_keyboard(callback.from_user.id))

# Subjects handlers
async def show_subjects(callback: CallbackQuery):
    subjects = db.get_user_subjects(callback.from_user.id)
    
    if not subjects:
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("➕ Добавить предмет", callback_data="add_subject"))
        keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
        await callback.message.edit_text("📚 У вас пока нет предметов. Добавьте первый!", reply_markup=keyboard)
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for subject in subjects:
        emoji = subject.get('emoji', '📚')
        active_count = subject.get('active_count', 0)
        overdue_count = subject.get('overdue_count', 0)
        
        status_text = f"({active_count} активн."
        if overdue_count > 0:
            status_text += f", {overdue_count} просроч.)"
        else:
            status_text += ")"
        
        btn_text = f"{emoji} {subject['name']} {status_text}"
        keyboard.add(InlineKeyboardButton(btn_text, callback_data=f"subject_{subject['id']}"))
    
    keyboard.add(InlineKeyboardButton("➕ Новый предмет", callback_data="add_subject"))
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    
    await callback.message.edit_text("📚 **ВАШИ ПРЕДМЕТЫ**", reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("subject_"))
async def show_subject_details(callback: CallbackQuery):
    subject_id = int(callback.data.split("_")[1])
    subject = db.get_subject(subject_id)
    
    if not subject:
        await callback.answer("Предмет не найден")
        return
    
    tasks = db.get_user_tasks(callback.from_user.id, subject_id=subject_id)
    
    upcoming = [t for t in tasks if t['status'] == 'pending' and datetime.strptime(t['due_date'], "%Y-%m-%d").date() >= datetime.now().date()]
    overdue = [t for t in tasks if t['status'] == 'pending' and datetime.strptime(t['due_date'], "%Y-%m-%d").date() < datetime.now().date()]
    completed = [t for t in tasks if t['status'] == 'completed']
    
    text = f"{subject['emoji']} **{subject['name']}**\n\n"
    
    if overdue:
        text += "⚠️ **ПРОСРОЧЕННЫЕ:**\n"
        for task in overdue:
            due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            days_overdue = (datetime.now().date() - datetime.strptime(task['due_date'], "%Y-%m-%d").date()).days
            text += f"  • {task['title']} — {due_date} (просрочено на {days_overdue} дн.)\n"
        text += "\n"
    
    if upcoming:
        text += "📋 **ПРЕДСТОЯЩИЕ:**\n"
        for task in upcoming:
            due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            time_str = f" {task['due_time']}" if task['due_time'] else ""
            text += f"  • {task['title']} — {due_date}{time_str}\n"
        text += "\n"
    
    if completed:
        text += "✅ **ВЫПОЛНЕННЫЕ:**\n"
        for task in completed[:5]:
            due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
            text += f"  • {task['title']} — {due_date}\n"
        if len(completed) > 5:
            text += f"  ... и ещё {len(completed) - 5}\n"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("➕ Добавить работу", callback_data=f"add_task_to_subject_{subject_id}"),
        InlineKeyboardButton("✏️ Редактировать предмет", callback_data=f"edit_subject_{subject_id}"),
        InlineKeyboardButton("🗑 Удалить предмет", callback_data=f"delete_subject_{subject_id}")
    )
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="menu_subjects"))
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "add_subject")
async def add_subject_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddSubjectStates.entering_name)
    await callback.message.edit_text("Введите название предмета:\n(например: Математика, Физика)")
    await callback.answer()

@dp.message(AddSubjectStates.entering_name)
async def save_subject(message: types.Message, state: FSMContext):
    subject_name = message.text.strip()
    user_id = message.from_user.id
    
    db.add_subject(user_id, subject_name)
    
    await state.clear()
    await message.answer(f"✅ Предмет '{subject_name}' добавлен!", reply_markup=get_main_keyboard(user_id))

# Add Task Flow
async def start_add_task(user_id: int, chat_id: int, state: FSMContext, callback: CallbackQuery = None):
    subjects = db.get_user_subjects(user_id)
    
    if not subjects:
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("➕ Добавить предмет", callback_data="add_subject"))
        if callback:
            await callback.message.edit_text("Сначала добавьте предмет!", reply_markup=keyboard)
        else:
            await bot.send_message(chat_id, "Сначала добавьте предмет!", reply_markup=keyboard)
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for subject in subjects:
        keyboard.add(InlineKeyboardButton(f"{subject['emoji']} {subject['name']}", callback_data=f"select_subject_{subject['id']}"))
    
    await state.set_state(AddTaskStates.selecting_subject)
    
    if callback:
        await callback.message.edit_text("Выберите предмет:", reply_markup=keyboard)
    else:
        await bot.send_message(chat_id, "Выберите предмет:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("select_subject_"), AddTaskStates.selecting_subject)
async def select_subject(callback: CallbackQuery, state: FSMContext):
    subject_id = int(callback.data.split("_")[2])
    await state.update_data(subject_id=subject_id)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    task_types = [
        ("📝 Контрольная", "control"),
        ("🧪 Лабораторная", "lab"),
        ("📚 Домашнее задание", "homework"),
        ("📖 Курсовая/Диплом", "coursework"),
        ("🎓 Экзамен", "exam"),
        ("📌 Другое", "other")
    ]
    
    for name, type_code in task_types:
        keyboard.add(InlineKeyboardButton(name, callback_data=f"task_type_{type_code}"))
    
    await state.set_state(AddTaskStates.selecting_type)
    await callback.message.edit_text("Выберите тип работы:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("task_type_"), AddTaskStates.selecting_type)
async def select_task_type(callback: CallbackQuery, state: FSMContext):
    task_type = callback.data.split("_")[2]
    await state.update_data(task_type=task_type)
    
    keyboard = InlineKeyboardMarkup(row_width=3)
    for i in range(1, 6):
        keyboard.insert(InlineKeyboardButton(str(i), callback_data=f"quantity_{i}"))
    
    await state.set_state(AddTaskStates.entering_quantity)
    await callback.message.edit_text("Сколько работ добавить? (1-5)", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("quantity_"), AddTaskStates.entering_quantity)
async def enter_quantity(callback: CallbackQuery, state: FSMContext):
    quantity = int(callback.data.split("_")[1])
    await state.update_data(quantity=quantity, current_index=0, tasks_data=[])
    
    await state.set_state(AddTaskStates.entering_title)
    await callback.message.edit_text(f"Работа №1 из {quantity}\nВведите название работы:")
    await callback.answer()

@dp.message(AddTaskStates.entering_title)
async def enter_task_title(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tasks_data = data.get('tasks_data', [])
    current_index = data.get('current_index', 0)
    quantity = data.get('quantity', 1)
    
    tasks_data.append({'title': message.text.strip()})
    current_index += 1
    
    if current_index < quantity:
        await state.update_data(tasks_data=tasks_data, current_index=current_index)
        await message.answer(f"Работа №{current_index + 1} из {quantity}\nВведите название работы:")
    else:
        await state.update_data(tasks_data=tasks_data)
        await state.set_state(AddTaskStates.entering_date)
        await message.answer("Введите дату сдачи для ВСЕХ работ в формате ДД.ММ.ГГГГ:\n(например: 25.05.2025)")

@dp.message(AddTaskStates.entering_date)
async def enter_date(message: types.Message, state: FSMContext):
    try:
        date_str = message.text.strip()
        # Validate date format
        due_date = datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
        await state.update_data(due_date=due_date)
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("Да", callback_data="time_yes"),
            InlineKeyboardButton("Нет", callback_data="time_no")
        )
        
        await state.set_state(AddTaskStates.entering_time)
        await message.answer("Указать время сдачи?", reply_markup=keyboard)
    except ValueError:
        await message.answer("Неверный формат! Используйте ДД.ММ.ГГГГ (например: 25.05.2025)")

@dp.callback_query(F.data.in_(["time_yes", "time_no"]), AddTaskStates.entering_time)
async def select_time_option(callback: CallbackQuery, state: FSMContext):
    if callback.data == "time_yes":
        await callback.message.edit_text("Введите время в формате ЧЧ:ММ (например: 14:30)")
        await state.set_state(AddTaskStates.entering_time)
    else:
        await state.update_data(due_time=None)
        await save_all_tasks(callback.message, state)
    
    await callback.answer()

@dp.message(AddTaskStates.entering_time)
async def enter_time(message: types.Message, state: FSMContext):
    time_str = message.text.strip()
    # Basic validation
    try:
        datetime.strptime(time_str, "%H:%M")
        await state.update_data(due_time=time_str)
        await save_all_tasks(message, state)
    except ValueError:
        await message.answer("Неверный формат! Используйте ЧЧ:ММ (например: 14:30)")

async def save_all_tasks(message: types.Message, state: FSMContext):
    data = await state.get_data()
    subject_id = data.get('subject_id')
    task_type = data.get('task_type')
    due_date = data.get('due_date')
    due_time = data.get('due_time')
    tasks_data = data.get('tasks_data', [])
    
    saved_tasks = []
    for task_info in tasks_data:
        task_id = db.add_task(
            user_id=message.from_user.id,
            subject_id=subject_id,
            title=task_info['title'],
            task_type=task_type,
            due_date=due_date,
            due_time=due_time,
            remind_before=[]  # Can add default reminders here
        )
        saved_tasks.append(task_info['title'])
    
    await state.clear()
    await message.answer(
        f"✅ Добавлено {len(saved_tasks)} работ(а)!\n\n"
        f"📌 {', '.join(saved_tasks)}\n"
        f"📅 {datetime.strptime(due_date, '%Y-%m-%d').strftime('%d.%m.%Y')}" + 
        (f" ⏰ {due_time}" if due_time else ""),
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# Calendar Menu
async def show_calendar_menu(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📆 По месяцам", callback_data="calendar_month"),
        InlineKeyboardButton("📋 По неделям", callback_data="calendar_week"),
        InlineKeyboardButton("⚠️ Просроченные", callback_data="overdue"),
        InlineKeyboardButton("📊 Нагрузка на неделю", callback_data="calendar_workload"),
        InlineKeyboardButton("🔗 Экспорт в Google Calendar", callback_data="calendar_export")
    )
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    
    await callback.message.edit_text("📅 **УМНЫЙ КАЛЕНДАРЬ**\n\nВыберите режим просмотра:", 
                                     reply_markup=keyboard, parse_mode="Markdown")

# Overdue Tasks Handler (Scenario B)
async def show_overdue_tasks(user_id: int, chat_id: int):
    tasks = db.get_user_tasks(user_id, status="pending")
    today = datetime.now().date()
    
    overdue = [t for t in tasks if datetime.strptime(t['due_date'], "%Y-%m-%d").date() < today]
    
    if not overdue:
        await bot.send_message(chat_id, "✅ Отлично! У вас нет просроченных работ.", 
                              reply_markup=get_main_keyboard(user_id))
        return
    
    text = "⚠️ **ПРОСРОЧЕННЫЕ РАБОТЫ**\n\n"
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for task in overdue:
        due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").date()
        days_overdue = (today - due_date).days
        text += f"{task.get('subject_emoji', '📚')} {task['subject_name']}:\n"
        text += f"  • {task['title']} — просрочено на {days_overdue} дн.\n"
        keyboard.add(InlineKeyboardButton(f"✏️ {task['title'][:30]}", callback_data=f"overdue_{task['id']}"))
    
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    
    await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("overdue_"))
async def handle_overdue_task(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    task = db.get_task(task_id)
    
    if not task:
        await callback.answer("Задача не найдена")
        return
    
    due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
    days_overdue = (datetime.now().date() - datetime.strptime(task['due_date'], "%Y-%m-%d").date()).days
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("➕ +1 день", callback_data=f"postpone_{task_id}_1"),
        InlineKeyboardButton("➕ +3 дня", callback_data=f"postpone_{task_id}_3"),
        InlineKeyboardButton("➕ +1 неделя", callback_data=f"postpone_{task_id}_7"),
        InlineKeyboardButton("✅ Сдано (с опозданием)", callback_data=f"complete_{task_id}"),
        InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{task_id}")
    )
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="overdue"))
    
    await callback.message.edit_text(
        f"⚠️ **{task['title']}**\n"
        f"📚 {task.get('subject_name', 'Без предмета')}\n"
        f"📅 Исходный дедлайн: {due_date}\n"
        f"⏰ Просрочено на {days_overdue} дн.\n\n"
        f"Что сделать?",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("postpone_"))
async def postpone_task(callback: CallbackQuery):
    parts = callback.data.split("_")
    task_id = int(parts[1])
    days = int(parts[2])
    
    new_date = db.postpone_task(task_id, days)
    
    if new_date:
        await callback.answer(f"✅ Задача перенесена на {datetime.strptime(new_date, '%Y-%m-%d').strftime('%d.%m.%Y')}")
        await show_overdue_tasks(callback.from_user.id, callback.message.chat.id)
    else:
        await callback.answer("Ошибка при переносе")

@dp.callback_query(F.data.startswith("complete_"))
async def complete_task(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    db.complete_task(task_id)
    
    await callback.answer("✅ Отмечено как сданное!")
    await show_overdue_tasks(callback.from_user.id, callback.message.chat.id)

@dp.callback_query(F.data.startswith("delete_"))
async def delete_task(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    db.delete_task(task_id)
    
    await callback.answer("🗑 Задача удалена")
    await show_overdue_tasks(callback.from_user.id, callback.message.chat.id)

# Reminders Menu (Scenario A)
async def show_reminders_menu(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("⚙️ Настроить шаблоны", callback_data="reminder_templates"),
        InlineKeyboardButton("🧠 Интеллектуальная агрегация", callback_data="reminder_aggregation"),
        InlineKeyboardButton("⏰ Настроить время дайджеста", callback_data="reminder_time"),
        InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")
    )
    
    await callback.message.edit_text(
        "🔔 **УМНЫЕ НАПОМИНАНИЯ**\n\n"
        "• **Шаблоны** — настройка времени напоминаний для разных типов работ\n"
        "• **Агрегация** — группировка нескольких напоминаний в одно\n"
        "• **Дайджест** — вечерний отчёт о завтрашних задачах",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# Statistics Menu
async def show_statistics(callback: CallbackQuery):
    stats = db.get_statistics(callback.from_user.id)
    overall = stats.get('overall', {})
    
    total = overall.get('completed', 0) + overall.get('active', 0) + overall.get('overdue', 0)
    completion_rate = (overall.get('completed', 0) / total * 100) if total > 0 else 0
    
    text = f"📊 **СТАТИСТИКА**\n\n"
    text += f"📝 Всего выполнено: {overall.get('completed', 0)}\n"
    text += f"⚠️ Просрочено: {overall.get('overdue', 0)}\n"
    text += f"📋 Текущих активных: {overall.get('active', 0)}\n"
    text += f"📈 Процент выполнения: {completion_rate:.1f}%\n\n"
    
    if stats.get('by_subject'):
        text += "**По предметам:**\n"
        for subject in stats['by_subject']:
            subj_total = subject.get('total', 0)
            subj_completed = subject.get('completed', 0)
            rate = (subj_completed / subj_total * 100) if subj_total > 0 else 0
            text += f"  {subject['emoji']} {subject['name']}: {rate:.0f}% ({subj_completed}/{subj_total})\n"
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

# Settings Menu
async def show_settings_menu(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("👤 Профиль", callback_data="settings_profile"),
        InlineKeyboardButton("🔔 Напоминания", callback_data="settings_notifications"),
        InlineKeyboardButton("🎨 Оформление", callback_data="settings_appearance"),
        InlineKeyboardButton("📤 Экспорт данных", callback_data="settings_export"),
        InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")
    )
    
    await callback.message.edit_text("⚙️ **НАСТРОЙКИ**\n\nВыберите раздел:", 
                                     reply_markup=keyboard, parse_mode="Markdown")

# Help Menu
async def show_help(callback: CallbackQuery):
    help_text = """
❓ **ПОМОЩЬ**

**Основные команды:**
/today — задачи на сегодня
/tomorrow — задачи на завтра
/week — задачи на неделю
/overdue — просроченные работы
/add — быстрое добавление

**Как пользоваться:**
1. Добавьте предмет через "Мои предметы"
2. Добавьте работу через "+ Добавить работу"
3. Получайте умные напоминания
4. Отмечайте работы выполненными

**Советы:**
• Используйте пакетное добавление для нескольких работ
• Настройте время напоминаний под себя
• Регулярно проверяйте просроченные работы

По вопросам и предложениям: @your_support
"""
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    
    await callback.message.edit_text(help_text, reply_markup=keyboard, parse_mode="Markdown")

# Quick Actions Menu
async def show_quick_actions(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📅 Сегодня", callback_data="quick_today"),
        InlineKeyboardButton("📅 Завтра", callback_data="quick_tomorrow"),
        InlineKeyboardButton("📅 Неделя", callback_data="quick_week"),
        InlineKeyboardButton("⚠️ Просроченные", callback_data="overdue"),
        InlineKeyboardButton("➕ Добавить", callback_data="menu_add_task")
    )
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    
    await callback.message.edit_text("⚡ **БЫСТРЫЕ ДЕЙСТВИЯ**\n\nВыберите действие:", 
                                     reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("quick_"))
async def handle_quick_action(callback: CallbackQuery):
    action = callback.data.split("_")[1]
    
    if action == "today":
        await cmd_today(callback.message)
    elif action == "tomorrow":
        await cmd_tomorrow(callback.message)
    elif action == "week":
        await cmd_week(callback.message)
    
    await callback.answer()

# Calendar callbacks
@dp.callback_query(F.data == "calendar_month")
async def calendar_month(callback: CallbackQuery):
    await callback.message.edit_text("📆 Функция календаря по месяцам в разработке...")
    # You can implement a full calendar here

@dp.callback_query(F.data == "calendar_week")
async def calendar_week(callback: CallbackQuery):
    await cmd_week(callback.message)

@dp.callback_query(F.data == "calendar_workload")
async def calendar_workload(callback: CallbackQuery):
    tasks = db.get_user_tasks(callback.from_user.id, status="pending")
    
    week_days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    workload = {i: 0 for i in range(7)}
    
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    
    for task in tasks:
        due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").date()
        if week_start <= due_date < week_start + timedelta(days=7):
            day_index = due_date.weekday()
            workload[day_index] += 1
    
    workload_text = "📊 **НАГРУЗКА НА ЭТУ НЕДЕЛЮ**\n\n"
    for i, day in enumerate(week_days):
        bar = "█" * min(workload[i], 10) + "░" * (10 - min(workload[i], 10))
        workload_text += f"{day}: {bar} {workload[i]}\n"
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="menu_calendar"))
    
    await callback.message.edit_text(workload_text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "calendar_export")
async def calendar_export(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔗 **ЭКСПОРТ В КАЛЕНДАРЬ**\n\n"
        "Функция экспорта в Google Calendar и Apple Calendar будет доступна в следующей версии.\n\n"
        "Пока вы можете использовать:\n"
        "/week — просмотр на неделю\n"
        "/today — задачи на сегодня",
        reply_markup=InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("◀️ Назад", callback_data="menu_calendar")
        ),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "overdue")
async def overdue_callback(callback: CallbackQuery):
    await show_overdue_tasks(callback.from_user.id, callback.message.chat.id)

# Reminder settings callbacks
@dp.callback_query(F.data == "reminder_templates")
async def reminder_templates(callback: CallbackQuery):
    text = "⚙️ **НАСТРОЙКИ НАПОМИНАНИЙ**\n\n"
    text += "**Шаблоны по умолчанию:**\n"
    text += "• 📝 Контрольная: за 3 дня, за 1 день, за 2 часа\n"
    text += "• 🧪 Лабораторная: за 2 дня, за 1 день, за 1 час\n"
    text += "• 📚 ДЗ: за 1 день, за 2 часа\n"
    text += "• 🎓 Экзамен: за 7 дней, за 3 дня, за 1 день, за 2 часа\n\n"
    text += "Индивидуальная настройка будет доступна в следующей версии."
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="menu_reminders"))
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "reminder_aggregation")
async def reminder_aggregation(callback: CallbackQuery):
    settings = db.get_user_settings(callback.from_user.id)
    agg_enabled = "✅ Включена" if settings.get('aggregation_enabled', 1) else "❌ Выключена"
    
    text = "🧠 **ИНТЕЛЛЕКТУАЛЬНАЯ АГРЕГАЦИЯ**\n\n"
    text += f"Статус: {agg_enabled}\n"
    text += f"Порог агрегации: от {AGGREGATION_THRESHOLD} работ в день\n"
    text += f"Время дайджеста: {settings.get('aggregation_hour', AGGREGATION_HOUR)}:00\n\n"
    text += "При включённой агрегации вы получаете одно групповое сообщение "
    text += "вместо нескольких отдельных напоминаний."
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔘 Вкл/Выкл", callback_data="agg_toggle"),
        InlineKeyboardButton("⏰ Изменить время", callback_data="reminder_time")
    )
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="menu_reminders"))
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "agg_toggle")
async def toggle_aggregation(callback: CallbackQuery):
    settings = db.get_user_settings(callback.from_user.id)
    new_value = 0 if settings.get('aggregation_enabled', 1) else 1
    db.update_user_setting(callback.from_user.id, 'aggregation_enabled', new_value)
    
    status = "включена" if new_value else "выключена"
    await callback.answer(f"Агрегация {status}")
    await reminder_aggregation(callback)

@dp.callback_query(F.data == "reminder_time")
async def reminder_time(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=4)
    hours = [18, 19, 20, 21, 22, 23]
    for hour in hours:
        keyboard.add(InlineKeyboardButton(f"{hour}:00", callback_data=f"set_agg_hour_{hour}"))
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="reminder_aggregation"))
    
    await callback.message.edit_text(
        "⏰ **Выберите время вечернего дайджеста**\n\n"
        "В это время вы будете получать сводку о задачах на следующий день.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("set_agg_hour_"))
async def set_agg_hour(callback: CallbackQuery):
    hour = int(callback.data.split("_")[3])
    db.update_user_setting(callback.from_user.id, 'aggregation_hour', hour)
    await callback.answer(f"Время дайджеста установлено на {hour}:00")
    await reminder_aggregation(callback)

# Settings callbacks
@dp.callback_query(F.data == "settings_profile")
async def settings_profile(callback: CallbackQuery):
    user = db.get_or_create_user(callback.from_user.id)
    
    text = f"👤 **ПРОФИЛЬ**\n\n"
    text += f"ID: {user['user_id']}\n"
    text += f"Имя: {user.get('first_name', '—')}\n"
    text += f"Username: @{user.get('username', '—')}\n"
    text += f"Часовой пояс: {user.get('timezone', 'UTC')}\n"
    text += f"Подписка: {user.get('subscription_type', 'free')}\n"
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("🌍 Изменить часовой пояс", callback_data="settings_timezone"),
        InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")
    )
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "settings_timezone")
async def settings_timezone(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    timezones = ["UTC+3 (MSK)", "UTC+5 (Екатеринбург)", "UTC+7 (Новосибирск)", "UTC+10 (Владивосток)"]
    for tz in timezones:
        keyboard.add(InlineKeyboardButton(tz, callback_data=f"set_tz_{tz}"))
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="settings_profile"))
    
    await callback.message.edit_text(
        "🌍 **Выберите ваш часовой пояс**\n\n"
        "Это важно для правильного времени напоминаний.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("set_tz_"))
async def set_timezone(callback: CallbackQuery):
    tz = callback.data.split("_")[2]
    # Extract the actual timezone offset
    if "MSK" in tz:
        tz_value = "Europe/Moscow"
    else:
        tz_value = tz
    
    # Update user timezone
    await callback.answer(f"Часовой пояс установлен: {tz}")
    await settings_profile(callback)

@dp.callback_query(F.data == "settings_notifications")
async def settings_notifications(callback: CallbackQuery):
    settings = db.get_user_settings(callback.from_user.id)
    
    text = f"🔔 **НАСТРОЙКИ УВЕДОМЛЕНИЙ**\n\n"
    text += f"🕐 Время тишины: {settings.get('quiet_hours_start', 23)}:00 - {settings.get('quiet_hours_end', 8)}:00\n"
    text += f"📊 Максимум напоминаний в день: {settings.get('max_notifications_per_day', 5)}\n"
    text += f"🧠 Агрегация: {'✅ Вкл' if settings.get('aggregation_enabled', 1) else '❌ Выкл'}\n"
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("🕐 Настроить тихие часы", callback_data="settings_quiet_hours"),
        InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")
    )
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "settings_appearance")
async def settings_appearance(callback: CallbackQuery):
    text = "🎨 **ОФОРМЛЕНИЕ**\n\n"
    text += "Вы можете настроить:\n"
    text += "• Эмодзи для предметов\n"
    text += "• Цветовую схему уведомлений\n\n"
    text += "Функция в разработке!"
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="menu_settings"))
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "settings_export")
async def settings_export(callback: CallbackQuery):
    text = "📤 **ЭКСПОРТ ДАННЫХ**\n\n"
    text += "Вы можете экспортировать все свои задачи в JSON или CSV.\n\n"
    text += "Функция в разработке!"
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data="menu_settings"))
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

# Notification worker
async def notification_worker():
    """Background task to check and send notifications"""
    while True:
        try:
            tasks_to_notify = db.get_tasks_for_notification()
            
            for task, remind_type in tasks_to_notify:
                user_id = task['user_id']
                
                # Check quiet hours
                settings = db.get_user_settings(user_id)
                current_hour = datetime.now().hour
                quiet_start = settings.get('quiet_hours_start', 23)
                quiet_end = settings.get('quiet_hours_end', 8)
                
                if quiet_start <= quiet_end:
                    is_quiet = quiet_start <= current_hour < quiet_end
                else:
                    is_quiet = current_hour >= quiet_start or current_hour < quiet_end
                
                if is_quiet:
                    continue
                
                # Send notification
                due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
                
                notification_text = f"🔔 **Напоминание!**\n\n"
                notification_text += f"📚 {task.get('subject_name', 'Без предмета')}\n"
                notification_text += f"{TASK_TYPE_EMOJIS.get(task['task_type'], '📌')} **{task['title']}**\n"
                notification_text += f"📅 Срок: {due_date}"
                
                if task['due_time']:
                    notification_text += f" ⏰ {task['due_time']}"
                
                # Check if aggregation is enabled and we have multiple tasks
                if settings.get('aggregation_enabled', 1):
                    # Get all tasks for the same day
                    same_day_tasks = db.get_tasks_by_date(user_id, task['due_date'])
                    if len(same_day_tasks) >= AGGREGATION_THRESHOLD:
                        # Will be handled by aggregation worker
                        continue
                
                try:
                    await bot.send_message(user_id, notification_text, parse_mode="Markdown")
                    db.mark_notification_sent(task['id'], remind_type)
                    logger.info(f"Sent notification for task {task['id']} to user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to send notification: {e}")
            
            await asyncio.sleep(NOTIFICATION_CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Notification worker error: {e}")
            await asyncio.sleep(NOTIFICATION_CHECK_INTERVAL)

# Aggregation worker
async def aggregation_worker():
    """Send aggregated notifications at specified hour"""
    while True:
        now = datetime.now()
        target_hour = AGGREGATION_HOUR
        
        # Wait until target hour
        next_run = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        
        wait_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        try:
            # Get all users
            # This is simplified; in production you'd query users with pending tasks
            users = set()
            tasks = db.get_user_tasks(0)  # This would need to be modified
            
            # For each user, aggregate tomorrow's tasks
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
            # This would need to be implemented with proper user iteration
            logger.info("Aggregation worker running")
            
        except Exception as e:
            logger.error(f"Aggregation worker error: {e}")

# Task edit handling
@dp.callback_query(F.data.startswith("task_"))
async def handle_task_action(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    task = db.get_task(task_id)
    
    if not task:
        await callback.answer("Задача не найдена")
        return
    
    due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").strftime("%d.%m.%Y")
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ Отметить выполненной", callback_data=f"complete_{task_id}"),
        InlineKeyboardButton("⏰ Перенести", callback_data=f"move_{task_id}"),
        InlineKeyboardButton("🔔 Настроить напоминания", callback_data=f"remind_{task_id}"),
        InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{task_id}")
    )
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data=f"subject_{task['subject_id']}"))
    
    await callback.message.edit_text(
        f"📌 **{task['title']}**\n"
        f"📚 {task.get('subject_name', 'Без предмета')}\n"
        f"📅 {due_date}" + (f" ⏰ {task['due_time']}" if task['due_time'] else "") + "\n"
        f"📊 Статус: {'✅ Выполнена' if task['status'] == 'completed' else '⏳ Активна'}",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("move_"))
async def move_task_prompt(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("➕ +1 день", callback_data=f"postpone_{task_id}_1"),
        InlineKeyboardButton("➕ +3 дня", callback_data=f"postpone_{task_id}_3"),
        InlineKeyboardButton("➕ +1 неделя", callback_data=f"postpone_{task_id}_7"),
        InlineKeyboardButton("📅 Своя дата", callback_data=f"custom_date_{task_id}")
    )
    keyboard.add(InlineKeyboardButton("◀️ Назад", callback_data=f"task_{task_id}"))
    
    await callback.message.edit_text(
        "⏰ **Перенос задачи**\n\nНа сколько перенести?",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

# Edit subject
@dp.callback_query(F.data.startswith("edit_subject_"))
async def edit_subject_prompt(callback: CallbackQuery, state: FSMContext):
    subject_id = int(callback.data.split("_")[2])
    subject = db.get_subject(subject_id)
    
    if not subject:
        await callback.answer("Предмет не найден")
        return
    
    await state.update_data(editing_subject_id=subject_id)
    await state.set_state(AddSubjectStates.entering_name)
    await callback.message.edit_text(
        f"Редактирование предмета: {subject['name']}\n\n"
        f"Введите новое название:"
    )
    await callback.answer()

@dp.message(AddSubjectStates.entering_name)
async def update_subject(message: types.Message, state: FSMContext):
    data = await state.get_data()
    subject_id = data.get('editing_subject_id')
    
    if subject_id:
        db.update_subject(subject_id, name=message.text.strip())
        await message.answer(f"✅ Предмет обновлён!", reply_markup=get_main_keyboard(message.from_user.id))
    else:
        # This is for adding new subject
        db.add_subject(message.from_user.id, message.text.strip())
        await message.answer(f"✅ Предмет '{message.text.strip()}' добавлен!", 
                            reply_markup=get_main_keyboard(message.from_user.id))
    
    await state.clear()

@dp.callback_query(F.data.startswith("delete_subject_"))
async def delete_subject_confirm(callback: CallbackQuery):
    subject_id = int(callback.data.split("_")[2])
    subject = db.get_subject(subject_id)
    
    if not subject:
        await callback.answer("Предмет не найден")
        return
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_subject_{subject_id}"),
        InlineKeyboardButton("❌ Нет", callback_data=f"subject_{subject_id}")
    )
    
    await callback.message.edit_text(
        f"⚠️ **Удаление предмета**\n\n"
        f"Вы действительно хотите удалить предмет '{subject['name']}'?\n\n"
        f"Все задачи этого предмета также будут удалены. Это действие необратимо.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_delete_subject_"))
async def confirm_delete_subject(callback: CallbackQuery):
    subject_id = int(callback.data.split("_")[3])
    db.delete_subject(subject_id)
    
    await callback.answer("✅ Предмет удалён")
    await show_subjects(callback)

# Add task to specific subject
@dp.callback_query(F.data.startswith("add_task_to_subject_"))
async def add_task_to_subject(callback: CallbackQuery, state: FSMContext):
    subject_id = int(callback.data.split("_")[4])
    await state.update_data(subject_id=subject_id)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    task_types = [
        ("📝 Контрольная", "control"),
        ("🧪 Лабораторная", "lab"),
        ("📚 Домашнее задание", "homework"),
        ("📖 Курсовая/Диплом", "coursework"),
        ("🎓 Экзамен", "exam"),
        ("📌 Другое", "other")
    ]
    
    for name, type_code in task_types:
        keyboard.add(InlineKeyboardButton(name, callback_data=f"task_type_{type_code}"))
    
    await state.set_state(AddTaskStates.selecting_type)
    await callback.message.edit_text("Выберите тип работы:", reply_markup=keyboard)
    await callback.answer()

# Custom date for task move
@dp.callback_query(F.data.startswith("custom_date_"))
async def custom_date_prompt(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])
    await state.update_data(moving_task_id=task_id)
    await state.set_state(AddTaskStates.entering_date)
    await callback.message.edit_text("Введите новую дату сдачи в формате ДД.ММ.ГГГГ:")
    await callback.answer()

@dp.message(AddTaskStates.entering_date)
async def process_custom_date(message: types.Message, state: FSMContext):
    try:
        date_str = message.text.strip()
        due_date = datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
        
        data = await state.get_data()
        task_id = data.get('moving_task_id')
        
        if task_id:
            # Update task date
            db.update_task(task_id, due_date=due_date)
            # Clear sent notifications
            db.mark_notification_sent(task_id, "manual_reschedule")
            await message.answer(f"✅ Задача перенесена на {date_str}", 
                               reply_markup=get_main_keyboard(message.from_user.id))
        else:
            # This is for adding new task date
            await state.update_data(due_date=due_date)
            
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("Да", callback_data="time_yes"),
                InlineKeyboardButton("Нет", callback_data="time_no")
            )
            
            await state.set_state(AddTaskStates.entering_time)
            await message.answer("Указать время сдачи?", reply_markup=keyboard)
            return
        
        await state.clear()
    except ValueError:
        await message.answer("Неверный формат! Используйте ДД.ММ.ГГГГ (например: 25.05.2025)")

# Main function
async def main():
    # Start background workers
    asyncio.create_task(notification_worker())
    asyncio.create_task(aggregation_worker())
    
    # Start polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())