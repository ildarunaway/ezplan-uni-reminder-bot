import telebot
from telebot import types
import sqlite3
from datetime import datetime, timedelta
import threading
import time
import json
import os
import logging

# ========== НАСТРОЙКА ==========
TOKEN = os.getenv('BOT_TOKEN')
print("ENV:", dict(os.environ))
print(f"DEBUG: token exists: {bool(os.getenv('BOT_TOKEN'))}")
print("ENV BOT_TOKEN:", os.getenv("BOT_TOKEN"))
if not TOKEN:
    print("Ошибка: переменная BOT_TOKEN не установлена!")
    exit(1)

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('university_bot.db', check_same_thread=False)
cursor = conn.cursor()

# Создание всех таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    timezone TEXT DEFAULT 'UTC',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT,
    emoji TEXT DEFAULT '📚',
    color TEXT DEFAULT '#3498db',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, name)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    subject_id INTEGER,
    title TEXT,
    task_type TEXT,
    description TEXT,
    due_date DATE,
    due_time TIME,
    remind_before TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS notifications_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    remind_type TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    UNIQUE(task_id, remind_type)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    aggregation_enabled INTEGER DEFAULT 1,
    aggregation_hour INTEGER DEFAULT 20,
    quiet_hours_start INTEGER DEFAULT 23,
    quiet_hours_end INTEGER DEFAULT 8,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
''')
conn.commit()

# ========== ЭМОДЗИ ==========
TASK_TYPE_EMOJIS = {
    "control": "📝",
    "lab": "🧪", 
    "homework": "📚",
    "exam": "🎓",
    "coursework": "📖",
    "other": "📌"
}

SUBJECT_EMOJIS = ["📐", "⚛️", "💻", "🇬🇧", "📜", "🎨", "🔬", "💊", "⚖️", "📊", "🎵", "🏛️"]

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_user_settings(user_id):
    cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
    settings = cursor.fetchone()
    if not settings:
        cursor.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return {"user_id": user_id, "aggregation_enabled": 1, "aggregation_hour": 20, "quiet_hours_start": 23, "quiet_hours_end": 8}
    return {"user_id": settings[0], "aggregation_enabled": settings[1], "aggregation_hour": settings[2], "quiet_hours_start": settings[3], "quiet_hours_end": settings[4]}

def get_or_create_user(user_id, username=None, first_name=None, last_name=None):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
                      (user_id, username, first_name, last_name))
        conn.commit()

def format_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y") if date_str else ""

def parse_date(date_str):
    return datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")

# ========== ГЛАВНОЕ МЕНЮ ==========
def get_main_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("📚 МОИ ПРЕДМЕТЫ", callback_data="menu_subjects"),
        types.InlineKeyboardButton("➕ БЫСТРОЕ ДОБАВЛЕНИЕ", callback_data="menu_add")
    )
    keyboard.add(
        types.InlineKeyboardButton("📅 УМНЫЙ КАЛЕНДАРЬ", callback_data="menu_calendar"),
        types.InlineKeyboardButton("🔔 УМНЫЕ НАПОМИНАНИЯ", callback_data="menu_reminders")
    )
    keyboard.add(
        types.InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data="menu_settings"),
        types.InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="menu_stats")
    )
    keyboard.add(
        types.InlineKeyboardButton("❓ ПОМОЩЬ", callback_data="menu_help"),
        types.InlineKeyboardButton("⚡ БЫСТРЫЕ ДЕЙСТВИЯ", callback_data="menu_quick")
    )
    return keyboard

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@bot.message_handler(commands=['start'])
def start(message):
    get_or_create_user(message.from_user.id, message.from_user.username,
                      message.from_user.first_name, message.from_user.last_name)
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("🎓 НАЧАТЬ", callback_data="menu_subjects")
    )
    
    bot.send_message(message.chat.id,
        f"🎓 привет, {message.from_user.first_name.upper()}!\n\n"
        f"я — твой умный помощник по учёбе. нажимай на кнопку ниже, чтобы начать! 👇",
        reply_markup=keyboard, parse_mode="Markdown")

@bot.message_handler(commands=['today'])
def today(message):
    today_str = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        SELECT t.*, s.name, s.emoji FROM tasks t
        LEFT JOIN subjects s ON t.subject_id = s.id
        WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending'
        ORDER BY t.due_time
    ''', (message.from_user.id, today_str))
    tasks = cursor.fetchall()
    
    tasks = [t for t in tasks if t[0] is not None]  # Отфильтруй NULL-строки
    
    if not tasks:
        bot.send_message(message.chat.id, "📅 НА СЕГОДНЯ\n\nНет запланированных работ. Отдыхайте! 🎉")
        return
    
    text = f"📅 **НА СЕГОДНЯ ({datetime.now().strftime('%d.%m.%Y')})**\n\n"
    for task in tasks:
        time_str = f" — {task[6]}" if task[6] else ""
        text += f"{task[12]} **{task[11]}**:\n  {TASK_TYPE_EMOJIS.get(task[4], '📌')} {task[3]}{time_str}\n\n"
    
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['tomorrow'])
def tomorrow(message):
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    cursor.execute('''
        SELECT t.*, s.name, s.emoji FROM tasks t
        LEFT JOIN subjects s ON t.subject_id = s.id
        WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending'
    ''', (message.from_user.id, tomorrow_str))
    tasks = cursor.fetchall()

    tasks = [t for t in tasks if t[0] is not None]
    
    if not tasks:
        bot.send_message(message.chat.id, f"📅 НА ЗАВТРА ({(datetime.now() + timedelta(days=1)).strftime('%d.%m.%Y')})\n\nНет запланированных работ.")
        return
    
    text = f"📅 **НА ЗАВТРА ({(datetime.now() + timedelta(days=1)).strftime('%d.%m.%Y')})**\n\n"
    for task in tasks:
        text += f"{task[12]} **{task[11]}**:\n  {TASK_TYPE_EMOJIS.get(task[4], '📌')} {task[3]}\n\n"
    
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['week'])
def week(message):
    today = datetime.now().date()
    week_end = today + timedelta(days=7)
    cursor.execute('''
        SELECT t.*, s.name, s.emoji FROM tasks t
        LEFT JOIN subjects s ON t.subject_id = s.id
        WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = 'pending'
        ORDER BY t.due_date
    ''', (message.from_user.id, today.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")))
    tasks = cursor.fetchall()

    tasks = [t for t in tasks if t[0] is not None]
    
    if not tasks:
        bot.send_message(message.chat.id, "📅 НА ЭТУ НЕДЕЛЮ\n\nНет запланированных работ.")
        return
    
    text = "📅 **ЗАДАЧИ НА ЭТУ НЕДЕЛЮ**\n\n"
    for task in tasks:
        due_date = format_date(task[5])
        text += f"{task[12]} {task[11]}: {task[3]} — {due_date}\n"
    
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['overdue'])
def overdue_command(message):
    show_overdue_tasks(message.from_user.id, message.chat.id)

@bot.message_handler(commands=['add'])
def add_command(message):
    start_add_flow(message.from_user.id, message.chat.id)

# ========== МЕНЮ И НАВИГАЦИЯ ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def menu_callback(call):
    action = call.data.split("_")[1]
    
    if action == "subjects":
        show_subjects(call)
    elif action == "add":
        start_add_flow(call.from_user.id, call.message.chat.id, call)
    elif action == "calendar":
        show_calendar_menu(call)
    elif action == "reminders":
        show_reminders_menu(call)
    elif action == "settings":
        show_settings_menu(call)
    elif action == "stats":
        show_statistics(call)
    elif action == "help":
        show_help(call)
    elif action == "quick":
        show_quick_actions(call)
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def back_to_menu(call):
    bot.edit_message_text("Главное меню:", call.message.chat.id, call.message.message_id,
                         reply_markup=get_main_keyboard())

# ========== ПРЕДМЕТЫ ==========
def show_subjects(call):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ? ORDER BY name", (call.from_user.id,))
    subjects = cursor.fetchall()
    
    if not subjects:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"))
        keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
        bot.edit_message_text("📚 **У вас пока нет предметов**\n\nДобавьте первый предмет, чтобы начать!",
                             call.message.chat.id, call.message.message_id, reply_markup=keyboard, parse_mode="Markdown")
        return
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for subject in subjects:
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE subject_id = ? AND status = 'pending' AND due_date >= date('now')", (subject[0],))
        upcoming = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE subject_id = ? AND status = 'pending' AND due_date < date('now')", (subject[0],))
        overdue = cursor.fetchone()[0]
        
        status = f"({upcoming} акт."
        if overdue > 0:
            status += f", {overdue} просроч.)"
        else:
            status += ")"
        
        keyboard.add(types.InlineKeyboardButton(f"{subject[2]} {subject[1]} {status}", callback_data=f"subject_{subject[0]}"))
    
    keyboard.add(types.InlineKeyboardButton("➕ НОВЫЙ ПРЕДМЕТ", callback_data="add_subject"))
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.edit_message_text("📚 **ВАШИ ПРЕДМЕТЫ**", call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("subject_"))
def show_subject_details(call):
    subject_id = int(call.data.split("_")[1])
    cursor.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
    subject = cursor.fetchone()
    if not subject:
        bot.answer_callback_query(call.id, "Предмет не найден")
        return
    
    cursor.execute('''
        SELECT t.*, s.name, s.emoji FROM tasks t
        LEFT JOIN subjects s ON t.subject_id = s.id
        WHERE t.user_id = ? AND t.subject_id = ?
        ORDER BY t.due_date
    ''', (call.from_user.id, subject_id))
    tasks = cursor.fetchall()

    tasks = [t for t in tasks if t[0] is not None]
    
    upcoming = []
    overdue = []
    completed = []
    
    for task in tasks:
        due_date = datetime.strptime(task[5], "%Y-%m-%d").date()
        if task[7] == 'completed':
            completed.append(task)
        elif due_date < datetime.now().date():
            overdue.append(task)
        else:
            upcoming.append(task)
    
    text = f"{subject[3]} **{subject[2]}**\n\n"
    
    if overdue:
        text += "⚠️ **ПРОСРОЧЕННЫЕ:**\n"
        for task in overdue:
            due_date = format_date(task[5])
            days = (datetime.now().date() - datetime.strptime(task[5], "%Y-%m-%d").date()).days
            text += f"  • {TASK_TYPE_EMOJIS.get(task[4], '📌')} {task[3]} — {due_date} (просрочено на {days} дн.)\n"
        text += "\n"
    
    if upcoming:
        text += "📋 **ПРЕДСТОЯЩИЕ:**\n"
        for task in upcoming:
            due_date = format_date(task[5])
            time_str = f" {task[6]}" if task[6] else ""
            text += f"  • {TASK_TYPE_EMOJIS.get(task[4], '📌')} {task[3]} — {due_date}{time_str}\n"
        text += "\n"
    
    if completed:
        text += "✅ **ВЫПОЛНЕННЫЕ:**\n"
        for task in completed[:5]:
            due_date = format_date(task[5])
            text += f"  • {task[3]} — {due_date}\n"
        if len(completed) > 5:
            text += f"  ... и ещё {len(completed) - 5}\n"
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("➕ ДОБАВИТЬ РАБОТУ", callback_data=f"add_to_subject_{subject_id}"),
        types.InlineKeyboardButton("✏️ РЕДАКТИРОВАТЬ", callback_data=f"edit_subject_{subject_id}"),
        types.InlineKeyboardButton("🗑 УДАЛИТЬ ПРЕДМЕТ", callback_data=f"del_subject_{subject_id}")
    )
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "add_subject")
def add_subject_prompt(call):
    msg = bot.send_message(call.message.chat.id, 
                           "📝 **Введите название предмета:**\n\nНапример: Математика, Физика, Программирование",
                           parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_subject, call.message.chat.id)
def save_subject(message, chat_id):
    subject_name = message.text.strip()
    if len(subject_name) > 50:
        bot.send_message(chat_id, "❌ Название слишком длинное (максимум 50 символов)")
        return
    
    try:
        cursor.execute("INSERT INTO subjects (user_id, name) VALUES (?, ?)", (message.from_user.id, subject_name))
        conn.commit()
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"))
        keyboard.add(types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        bot.send_message(chat_id, f"✅ Предмет **{subject_name}** добавлен!", 
                        reply_markup=keyboard, parse_mode="Markdown")
    except sqlite3.IntegrityError:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton("🔄 ПОПРОБОВАТЬ ЕЩЕ", callback_data="add_subject"))
        keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
        bot.send_message(chat_id, f"❌ Предмет **{subject_name}** уже существует!", 
                        reply_markup=keyboard, parse_mode="Markdown")
# ========== БЫСТРОЕ ДОБАВЛЕНИЕ (ПАКЕТНЫЙ РЕЖИМ) ==========
user_temp = {}

def start_add_flow(user_id, chat_id, call=None):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ?", (user_id,))
    subjects = cursor.fetchall()
    
    if not subjects:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"))
        if call:
            bot.edit_message_text("📚 **Сначала добавьте предмет!**", chat_id, call.message.message_id, reply_markup=keyboard, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "📚 **Сначала добавьте предмет!**", reply_markup=keyboard, parse_mode="Markdown")
        return
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    for subject in subjects:
        keyboard.add(types.InlineKeyboardButton(f"{subject[2]} {subject[1]}", callback_data=f"fast_subject_{subject[0]}"))
    
    if call:
        bot.edit_message_text("📌 **Выберите предмет:**", chat_id, call.message.message_id, reply_markup=keyboard, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, "📌 **Выберите предмет:**", reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_subject_"))
def fast_select_subject(call):
    subject_id = int(call.data.split("_")[2])
    user_temp[call.from_user.id] = {'subject_id': subject_id}
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    task_types = [
        ("📝 Контрольная", "control"),
        ("🧪 Лабораторная", "lab"),
        ("📚 Домашнее задание", "homework"),
        ("📖 Курсовая/Диплом", "coursework"),
        ("🎓 Экзамен", "exam"),
        ("📌 Другое", "other")
    ]
    for name, ttype in task_types:
        keyboard.add(types.InlineKeyboardButton(name, callback_data=f"fast_type_{ttype}"))
    
    bot.edit_message_text("📋 **Выберите тип работы:**", call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_type_"))
def fast_select_type(call):
    task_type = call.data.split("_")[2]
    user_temp[call.from_user.id]['task_type'] = task_type
    
    keyboard = types.InlineKeyboardMarkup(row_width=5)
    for i in range(1, 6):
        keyboard.add(types.InlineKeyboardButton(str(i), callback_data=f"fast_qty_{i}"))
    
    bot.edit_message_text("🔢 **Сколько работ добавить? (1-5)**", call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_qty_"))
def fast_quantity(call):
    quantity = int(call.data.split("_")[2])
    user_temp[call.from_user.id]['quantity'] = quantity
    user_temp[call.from_user.id]['tasks'] = []
    user_temp[call.from_user.id]['current'] = 0
    
    ask_task_title(call.from_user.id, call.message.chat.id, call)

def ask_task_title(user_id, chat_id, call=None):
    data = user_temp.get(user_id, {})
    current = data.get('current', 0)
    quantity = data.get('quantity', 1)
    
    if current >= quantity:
        save_all_tasks(user_id, chat_id)
        return
    
    data['current'] = current + 1
    user_temp[user_id] = data
    
    msg = bot.send_message(chat_id, f"📝 **Работа №{current + 1} из {quantity}**\n\nВведите название работы:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_task_title, user_id, chat_id)

def save_task_title(message, user_id, chat_id):
    data = user_temp.get(user_id, {})
    if 'tasks' not in data:
        data['tasks'] = []
    data['tasks'].append({'title': message.text.strip()})
    user_temp[user_id] = data
    
    msg = bot.send_message(chat_id, "📅 **Введите дату сдачи**\n\nФормат: ДД.ММ.ГГГГ", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_task_date_individual, user_id, chat_id)

def save_all_tasks(user_id, chat_id):
    data = user_temp.get(user_id, {})
    tasks = data.get('tasks', [])
    subject_id = data.get('subject_id')
    task_type = data.get('task_type')
    
    saved = []
    for task_info in tasks:
        due_date = task_info.get('due_date')
        due_time = task_info.get('due_time')
        
        cursor.execute('''
            INSERT INTO tasks (user_id, subject_id, title, task_type, due_date, due_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, subject_id, task_info['title'], task_type, due_date, due_time))
        saved.append(task_info['title'])
    
    conn.commit()
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="menu_add"))
    keyboard.add(types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"))
    keyboard.add(types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    
    task_list = '\n'.join([f"  • {t}" for t in saved])
    bot.send_message(chat_id, 
        f"✅ **Добавлено {len(saved)} работ(а)!**\n\n"
        f"{task_list}",
        reply_markup=keyboard, parse_mode="Markdown")
    
    if user_id in user_temp:
        del user_temp[user_id]

def save_task_date_individual(message, user_id, chat_id):
    try:
        due_date = parse_date(message.text.strip())
    except:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton("🔄 ПОПРОБОВАТЬ ЕЩЕ", callback_data="menu_add"))
        keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
        bot.send_message(chat_id, "❌ Неверный формат! Используйте ДД.ММ.ГГГГ", reply_markup=keyboard, parse_mode="Markdown")
        return
    
    data = user_temp.get(user_id, {})
    task_index = data.get('current', 1) - 1
    data['tasks'][task_index]['due_date'] = due_date
    user_temp[user_id] = data
    
    # Продолжить со следующей работы
    ask_task_title(user_id, chat_id)

def save_task_time_individual(message, user_id, chat_id):
    time_str = message.text.strip()
    if not (len(time_str) == 5 and time_str[2] == ':' and time_str[:2].isdigit() and time_str[3:].isdigit()):
        bot.send_message(chat_id, "❌ Неверный формат! Используйте ЧЧ:ММ")
        return
    
    data = user_temp.get(user_id, {})
    task_index = data.get('current', 1) - 1
    data['tasks'][task_index]['due_time'] = time_str
    user_temp[user_id] = data
    
    # Продолжить со следующей работы
    ask_task_title(user_id, chat_id)



@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_time_"))

    for task_info in tasks:
        cursor.execute('''
            INSERT INTO tasks (user_id, subject_id, title, task_type, due_date, due_time)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, subject_id, task_info['title'], task_type, due_date, due_time))
        saved.append(task_info['title'])
    
    conn.commit()
    
    time_text = f" ⏰ {due_time}" if due_time else ""
    bot.send_message(chat_id, 
        f"✅ **Добавлено {len(saved)} работ(а)!**\n\n"
        f"📌 {', '.join(saved)}\n"
        f"📅 {format_date(due_date)}{time_text}",
        reply_markup=get_main_keyboard(), parse_mode="Markdown")
    
    if user_id in user_temp:
        del user_temp[user_id]

# ========== ПРОСРОЧЕННЫЕ (СЦЕНАРИЙ Б) ==========
def show_overdue_tasks(user_id, chat_id):
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        SELECT t.*, s.name, s.emoji FROM tasks t
        LEFT JOIN subjects s ON t.subject_id = s.id
        WHERE t.user_id = ? AND t.due_date < ? AND t.status = 'pending'
        ORDER BY t.due_date
    ''', (user_id, today))
    tasks = cursor.fetchall()
    
    if not tasks:
        bot.send_message(chat_id, "✅ **Отлично! У вас нет просроченных работ.**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
        return
    
    text = "⚠️ **ПРОСРОЧЕННЫЕ РАБОТЫ**\n\n"
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    for task in tasks:
        due_date = datetime.strptime(task[5], "%Y-%m-%d").date()
        days_overdue = (datetime.now().date() - due_date).days
        text += f"{task[11]} {task[10]}:\n  • {task[3]} — просрочено на {days_overdue} дн.\n\n"
        keyboard.add(types.InlineKeyboardButton(f"✏️ {task[3][:30]}", callback_data=f"overdue_task_{task[0]}"))
    
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("overdue_task_"))
def handle_overdue_task(call):
    task_id = int(call.data.split("_")[2])
    cursor.execute("SELECT t.*, s.name FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.id = ?", (task_id,))
    task = cursor.fetchone()
    
    if not task:
        bot.answer_callback_query(call.id, "Задача не найдена")
        return
    
    due_date = format_date(task[5])
    days_overdue = (datetime.now().date() - datetime.strptime(task[5], "%Y-%m-%d").date()).days
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("📅 +1 ДЕНЬ", callback_data=f"postpone_{task_id}_1"),
        types.InlineKeyboardButton("📅 +3 ДНЯ", callback_data=f"postpone_{task_id}_3"),
        types.InlineKeyboardButton("📅 +1 НЕДЕЛЯ", callback_data=f"postpone_{task_id}_7"),
        types.InlineKeyboardButton("✅ СДАНО (С ОПОЗДАНИЕМ)", callback_data=f"complete_overdue_{task_id}"),
        types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"delete_overdue_{task_id}")
    )
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    
    bot.edit_message_text(
        f"⚠️ **{task[3]}**\n"
        f"📚 {task[11]}\n"
        f"📅 Исходный дедлайн: {due_date}\n"
        f"⏰ Просрочено на {days_overdue} дн.\n\n"
        f"**Что сделать?**",
        call.message.chat.id, call.message.message_id,
        reply_markup=keyboard, parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("postpone_"))
def postpone_task(call):
    parts = call.data.split("_")
    task_id = int(parts[1])
    days = int(parts[2])
    
    cursor.execute("SELECT due_date FROM tasks WHERE id = ?", (task_id,))
    old_date = cursor.fetchone()[0]
    new_date = (datetime.strptime(old_date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
    cursor.execute("UPDATE tasks SET due_date = ? WHERE id = ?", (new_date, task_id))
    cursor.execute("DELETE FROM notifications_sent WHERE task_id = ?", (task_id,))
    conn.commit()
    
    bot.answer_callback_query(call.id, f"✅ Перенесено на {format_date(new_date)}")
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("complete_overdue_"))
def complete_overdue_task(call):
    task_id = int(call.data.split("_")[2])
    cursor.execute("UPDATE tasks SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,))
    conn.commit()
    bot.answer_callback_query(call.id, "✅ Отмечено как сданное!")
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_overdue_"))
def delete_overdue_task(call):
    task_id = int(call.data.split("_")[2])
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    bot.answer_callback_query(call.id, "🗑 Задача удалена")
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

# ========== КАЛЕНДАРЬ ==========
def show_calendar_menu(call):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("📆 ПО МЕСЯЦАМ", callback_data="calendar_month"),
        types.InlineKeyboardButton("📋 ПО НЕДЕЛЯМ", callback_data="calendar_week"),
        types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="calendar_overdue"),
        types.InlineKeyboardButton("📊 НАГРУЗКА", callback_data="calendar_workload"),
        types.InlineKeyboardButton("🔗 ЭКСПОРТ", callback_data="calendar_export")
    )
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    
    bot.edit_message_text("📅 **УМНЫЙ КАЛЕНДАРЬ**\n\nВыберите режим просмотра:",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "calendar_month")
def calendar_month(call):
    bot.edit_message_text("📆 **Функция календаря по месяцам**\n\nБудет доступна в следующей версии!",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")
                         ), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "calendar_week")
def calendar_week(call):
    today = datetime.now().date()
    week_end = today + timedelta(days=7)
    cursor.execute('''
        SELECT t.*, s.name, s.emoji FROM tasks t
        LEFT JOIN subjects s ON t.subject_id = s.id
        WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = 'pending'
        ORDER BY t.due_date
    ''', (call.from_user.id, today.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")))
    tasks = cursor.fetchall()

    tasks = [t for t in tasks if t[0] is not None]
    
    if not tasks:
        bot.edit_message_text("📋 **НА ЭТУ НЕДЕЛЮ**\n\nНет запланированных работ.",
                             call.message.chat.id, call.message.message_id,
                             reply_markup=types.InlineKeyboardMarkup().add(
                                 types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")
                             ), parse_mode="Markdown")
        return
    
    text = "📋 **ЗАДАЧИ НА ЭТУ НЕДЕЛЮ**\n\n"
    current_date = None
    for task in tasks:
        due_date = datetime.strptime(task[5], "%Y-%m-%d").date()
        if current_date != due_date:
            current_date = due_date
            text += f"\n**{due_date.strftime('%A, %d.%m.%Y')}:**\n"
        text += f"  {task[12]} {task[11]}: {task[3]}\n"
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")
                         ), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "calendar_overdue")
def calendar_overdue(call):
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "calendar_workload")
def calendar_workload(call):
    week_days = ['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС']
    workload = {i: 0 for i in range(7)}
    
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    
    cursor.execute('''
        SELECT due_date FROM tasks
        WHERE user_id = ? AND status = 'pending'
    ''', (call.from_user.id,))
    
    for task in cursor.fetchall():
        due_date = datetime.strptime(task[0], "%Y-%m-%d").date()
        if week_start <= due_date < week_start + timedelta(days=7):
            workload[due_date.weekday()] += 1
    
    text = "📊 **НАГРУЗКА НА ЭТУ НЕДЕЛЮ**\n\n"
    for i, day in enumerate(week_days):
        bar = "█" * min(workload[i], 10) + "░" * (10 - min(workload[i], 10))
        text += f"{day}: {bar} {workload[i]}\n"
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")
                         ), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "calendar_export")
def calendar_export(call):
    bot.edit_message_text("🔗 **ЭКСПОРТ В КАЛЕНДАРЬ**\n\nФункция будет доступна в следующей версии.\n\nПока используйте:\n/today — задачи на сегодня\n/week — задачи на неделю",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")
                         ), parse_mode="Markdown")

# ========== НАПОМИНАНИЯ ==========
def show_reminders_menu(call):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("⚙️ ШАБЛОНЫ НАПОМИНАНИЙ", callback_data="reminder_templates"),
        types.InlineKeyboardButton("🧠 ИНТЕЛЛЕКТУАЛЬНАЯ АГРЕГАЦИЯ", callback_data="reminder_aggregation"),
        types.InlineKeyboardButton("⏰ ВРЕМЯ ДАЙДЖЕСТА", callback_data="reminder_time"),
        types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")
    )
    
    bot.edit_message_text("🔔 **УМНЫЕ НАПОМИНАНИЯ**\n\n"
                         "• **Шаблоны** — настройка времени напоминаний для разных типов работ\n"
                         "• **Агрегация** — группировка нескольких напоминаний в одно\n"
                         "• **Дайджест** — вечерний отчёт о задачах на завтра",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "reminder_templates")
def reminder_templates(call):
    text = "⚙️ **ШАБЛОНЫ НАПОМИНАНИЙ ПО УМОЛЧАНИЮ**\n\n"
    text += "• 📝 Контрольная: за 3 дня, за 1 день, за 2 часа\n"
    text += "• 🧪 Лабораторная: за 2 дня, за 1 день, за 1 час\n"
    text += "• 📚 ДЗ: за 1 день, за 2 часа\n"
    text += "• 🎓 Экзамен: за 7 дней, за 3 дня, за 1 день, за 2 часа\n"
    text += "• 📖 Курсовая: за 14 дней, за 7 дней, за 3 дня, за 1 день\n\n"
    text += "Индивидуальная настройка будет доступна в следующей версии."
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders")
                         ), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "reminder_aggregation")
def reminder_aggregation(call):
    settings = get_user_settings(call.from_user.id)
    status = "✅ ВКЛЮЧЕНА" if settings['aggregation_enabled'] else "❌ ВЫКЛЮЧЕНА"
    
    text = f"🧠 **ИНТЕЛЛЕКТУАЛЬНАЯ АГРЕГАЦИЯ**\n\n"
    text += f"Статус: {status}\n"
    text += f"Порог агрегации: от 2 работ в день\n"
    text += f"Время дайджеста: {settings['aggregation_hour']}:00\n\n"
    text += "При включённой агрегации вы получаете ОДНО групповое сообщение "
    text += "вместо нескольких отдельных напоминаний."
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("🔘 ВКЛ/ВЫКЛ", callback_data="agg_toggle"),
        types.InlineKeyboardButton("⏰ ИЗМЕНИТЬ ВРЕМЯ", callback_data="reminder_time")
    )
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "agg_toggle")
def toggle_aggregation(call):
    settings = get_user_settings(call.from_user.id)
    new_value = 0 if settings['aggregation_enabled'] else 1
    cursor.execute("UPDATE user_settings SET aggregation_enabled = ? WHERE user_id = ?", (new_value, call.from_user.id))
    conn.commit()
    
    status = "включена" if new_value else "выключена"
    bot.answer_callback_query(call.id, f"Агрегация {status}")
    reminder_aggregation(call)

@bot.callback_query_handler(func=lambda call: call.data == "reminder_time")
def reminder_time(call):
    keyboard = types.InlineKeyboardMarkup(row_width=4)
    hours = [18, 19, 20, 21, 22, 23]
    for hour in hours:
        keyboard.add(types.InlineKeyboardButton(f"{hour}:00", callback_data=f"set_hour_{hour}"))
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders"))
    
    bot.edit_message_text("⏰ **ВЫБЕРИТЕ ВРЕМЯ ВЕЧЕРНЕГО ДАЙДЖЕСТА**\n\n"
                         "В это время вы будете получать сводку о задачах на следующий день.",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_hour_"))
def set_agg_hour(call):
    hour = int(call.data.split("_")[2])
    cursor.execute("UPDATE user_settings SET aggregation_hour = ? WHERE user_id = ?", (hour, call.from_user.id))
    conn.commit()
    bot.answer_callback_query(call.id, f"Время дайджеста установлено на {hour}:00")
    reminder_aggregation(call)

# ========== СТАТИСТИКА ==========
def show_statistics(call):
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'completed'", (call.from_user.id,))
    completed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'pending'", (call.from_user.id,))
    pending = cursor.fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND due_date < ? AND status = 'pending'", (call.from_user.id, today))
    overdue = cursor.fetchone()[0]
    
    total = completed + pending + overdue
    completion_rate = (completed / total * 100) if total > 0 else 0
    
    text = f"📊 **СТАТИСТИКА**\n\n"
    text += f"✅ Всего выполнено: {completed}\n"
    text += f"⚠️ Просрочено: {overdue}\n"
    text += f"📋 Текущих активных: {pending}\n"
    text += f"📈 Процент выполнения: {completion_rate:.1f}%\n\n"
    
    cursor.execute('''
        SELECT s.name, s.emoji,
               COUNT(CASE WHEN t.status = 'completed' THEN 1 END) as comp,
               COUNT(*) as total
        FROM subjects s
        LEFT JOIN tasks t ON t.subject_id = s.id
        WHERE s.user_id = ?
        GROUP BY s.id
    ''', (call.from_user.id,))
    
    by_subject = cursor.fetchall()
    if by_subject:
        text += "**ПО ПРЕДМЕТАМ:**\n"
        for subject in by_subject:
            rate = (subject[2] / subject[3] * 100) if subject[3] > 0 else 0
            text += f"  {subject[1]} {subject[0]}: {rate:.0f}% ({subject[2]}/{subject[3]})\n"
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

# ========== НАСТРОЙКИ ==========
def show_settings_menu(call):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="settings_profile"),
        types.InlineKeyboardButton("🔔 НАПОМИНАНИЯ", callback_data="settings_notifications"),
        types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")
    )
    
    bot.edit_message_text("⚙️ **НАСТРОЙКИ**\n\nВыберите раздел:",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "settings_profile")
def settings_profile(call):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (call.from_user.id,))
    user = cursor.fetchone()
    
    text = f"👤 **ПРОФИЛЬ**\n\n"
    text += f"ID: {user[0]}\n"
    text += f"Имя: {user[2] or '—'} {user[3] or ''}\n"
    text += f"Username: @{user[1] or '—'}\n"
    text += f"Часовой пояс: {user[4]}\n"
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("🌍 ИЗМЕНИТЬ ЧАСОВОЙ ПОЯС", callback_data="settings_timezone"),
        types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings")
    )
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "settings_timezone")
def settings_timezone(call):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    timezones = ["UTC+3 (МСК)", "UTC+4 (Самара)", "UTC+5 (Екатеринбург)", "UTC+6 (Омск)",
                 "UTC+7 (Новосибирск)", "UTC+8 (Иркутск)", "UTC+9 (Якутск)", "UTC+10 (Владивосток)"]
    for tz in timezones:
        keyboard.add(types.InlineKeyboardButton(tz, callback_data=f"tz_{tz}"))
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="settings_profile"))
    
    bot.edit_message_text("🌍 **ВЫБЕРИТЕ ВАШ ЧАСОВОЙ ПОЯС**\n\n"
                         "Это важно для правильного времени напоминаний.",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("tz_"))
def set_timezone(call):
    tz = call.data.split("_")[1]
    cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (tz, call.from_user.id))
    conn.commit()
    bot.answer_callback_query(call.id, f"Часовой пояс установлен: {tz}")
    settings_profile(call)

@bot.callback_query_handler(func=lambda call: call.data == "settings_notifications")
def settings_notifications(call):
    settings = get_user_settings(call.from_user.id)
    
    text = f"🔔 **НАСТРОЙКИ УВЕДОМЛЕНИЙ**\n\n"
    text += f"🕐 Тихие часы: {settings['quiet_hours_start']}:00 - {settings['quiet_hours_end']}:00\n"
    text += f"🧠 Агрегация: {'✅ Вкл' if settings['aggregation_enabled'] else '❌ Выкл'}\n"
    text += f"⏰ Время дайджеста: {settings['aggregation_hour']}:00\n\n"
    text += "В следующей версии: настройка тихих часов и лимита уведомлений"
    
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

# ========== ПОМОЩЬ ==========
def show_help(call):
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
2. Добавьте работу через "Быстрое добавление"
3. Получайте умные напоминания
4. Отмечайте работы выполненными

**Советы:**
• Используйте пакетное добавление для нескольких работ
• Настройте время дайджеста в "Умные напоминания"
• Регулярно проверяйте просроченные работы

По вопросам: @your_support
"""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    
    bot.edit_message_text(help_text, call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

# ========== БЫСТРЫЕ ДЕЙСТВИЯ ==========
def show_quick_actions(call):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("📅 СЕГОДНЯ", callback_data="quick_today"),
        types.InlineKeyboardButton("📅 ЗАВТРА", callback_data="quick_tomorrow"),
        types.InlineKeyboardButton("📅 НЕДЕЛЯ", callback_data="quick_week"),
        types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="quick_overdue"),
        types.InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data="quick_add")
    )
    keyboard.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    
    bot.edit_message_text("⚡ **БЫСТРЫЕ ДЕЙСТВИЯ**\n\nВыберите действие:",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("quick_"))
def quick_action(call):
    action = call.data.split("_")[1]
    
    if action == "today":
        today(call.message)
    elif action == "tomorrow":
        tomorrow(call.message)
    elif action == "week":
        week(call.message)
    elif action == "overdue":
        show_overdue_tasks(call.from_user.id, call.message.chat.id)
    elif action == "add":
        start_add_flow(call.from_user.id, call.message.chat.id, call)
    
    bot.answer_callback_query(call.id)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("🚀 Бот запущен и работает на Render...")
    print(f"📊 Бот @{bot.get_me().username} готов к работе!")
    bot.infinity_polling(skip_pending=True)
