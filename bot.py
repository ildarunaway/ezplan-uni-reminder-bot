import telebot
from telebot import types
import sqlite3
from datetime import datetime, timedelta
import os
import logging

# ========== НАСТРОЙКА ==========
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    print("Ошибка: переменная BOT_TOKEN не установлена!")
    exit(1)

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('university_bot.db', check_same_thread=False)
conn.row_factory = sqlite3.Row  # Безопасный доступ к колонкам по имени
cursor = conn.cursor()

# Создание всех таблиц
cursor.executescript('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    timezone TEXT DEFAULT 'UTC',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT,
    emoji TEXT DEFAULT '📚',
    color TEXT DEFAULT '#3498db',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    subject_id INTEGER,
    title TEXT,
    task_type TEXT,
    description TEXT,
    due_date DATE,
    status TEXT DEFAULT 'pending',
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS notifications_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    remind_type TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    UNIQUE(task_id, remind_type)
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    aggregation_enabled INTEGER DEFAULT 1,
    aggregation_hour INTEGER DEFAULT 20,
    quiet_hours_start INTEGER DEFAULT 23,
    quiet_hours_end INTEGER DEFAULT 8,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
''')
conn.commit()

# ========== ЭМОДЗИ ==========
TASK_TYPE_EMOJIS = {
    "control": "📝 Контрольная",
    "lab": "🧪 Лабораторная",
    "homework": "📚 Домашнее задание",
    "exam": "🎓 Экзамен",
    "coursework": "📖 Курсовая/Диплом",
    "other": "📌 Другое"
}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_user_settings(user_id):
    cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
    settings = cursor.fetchone()
    if not settings:
        cursor.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return {"aggregation_enabled": 1, "aggregation_hour": 20}
    return {"aggregation_enabled": settings['aggregation_enabled'], "aggregation_hour": settings['aggregation_hour']}

def get_or_create_user(user_id, username=None, first_name=None, last_name=None):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
                       (user_id, username, first_name, last_name))
        conn.commit()

def format_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except:
        return date_str

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
        types.InlineKeyboardButton("📂 МОИ РАБОТЫ", callback_data="menu_my_tasks"),
        types.InlineKeyboardButton("⚡ БЫСТРЫЕ ДЕЙСТВИЯ", callback_data="menu_quick")
    )
    return keyboard

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@bot.message_handler(commands=['start'])
def start(message):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("🎓 НАЧАТЬ", callback_data="menu_subjects"))
    bot.send_message(message.chat.id,
        f"🎓 Привет, {message.from_user.first_name.upper()}!\n\n"
        f"Я — твой умный помощник по учёбе. Нажимай на кнопку ниже, чтобы начать! 👇",
        reply_markup=keyboard)

@bot.message_handler(commands=['today'])
def cmd_today(message):
    show_tasks_for_date(message.chat.id, message.from_user.id, datetime.now().date(), "СЕГОДНЯ")

@bot.message_handler(commands=['tomorrow'])
def cmd_tomorrow(message):
    show_tasks_for_date(message.chat.id, message.from_user.id, (datetime.now() + timedelta(days=1)).date(), "ЗАВТРА")

@bot.message_handler(commands=['week'])
def cmd_week(message):
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    show_weekly_tasks(message.chat.id, message.from_user.id, monday, sunday)

@bot.message_handler(commands=['overdue'])
def cmd_overdue(message):
    show_overdue_tasks(message.chat.id, message.from_user.id)

@bot.message_handler(commands=['add'])
def cmd_add(message):
    start_add_flow(message.from_user.id, message.chat.id)

# ========== УНИВЕРСАЛЬНЫЕ ФУНКЦИИ ОТОБРАЖЕНИЯ ==========
def show_tasks_for_date(chat_id, user_id, date, title):
    date_str = date.strftime("%Y-%m-%d")
    cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t
                      LEFT JOIN subjects s ON t.subject_id = s.id
                      WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending'
                      ORDER BY t.due_date''', (user_id, date_str))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(chat_id, f"📅 {title} ({format_date(date_str)})\n\nНет запланированных работ. Отдыхай! 🎉")
        return
    text = f"📅 **{title} ({format_date(date_str)})**\n\n"
    for t in tasks:
        text += f"{t['emoji']} **{t['name']}**\n  {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}\n  📅 Сдача: {format_date(t['due_date'])}\n\n"
    bot.send_message(chat_id, text, parse_mode="Markdown")

def show_weekly_tasks(chat_id, user_id, monday, sunday):
    m_str, s_str = monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")
    cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t
                      LEFT JOIN subjects s ON t.subject_id = s.id
                      WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = 'pending'
                      ORDER BY t.due_date''', (user_id, m_str, s_str))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(chat_id, f"📅 **НА НЕДЕЛЕ**\n({format_date(m_str)} - {format_date(s_str)})\n\nНет запланированных работ.")
        return
    text = f"📅 **НА НЕДЕЛЕ**\n({format_date(m_str)} - {format_date(s_str)})\n\n"
    current_date = None
    for t in tasks:
        if current_date != t['due_date']:
            current_date = t['due_date']
            text += f"\n**{format_date(current_date)}:**\n"
        text += f"  {t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}\n"
    bot.send_message(chat_id, text, parse_mode="Markdown")

def show_overdue_tasks(chat_id, user_id):
    today = datetime.now().date()
    cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t
                      LEFT JOIN subjects s ON t.subject_id = s.id
                      WHERE t.user_id = ? AND t.due_date < ? AND t.status = 'pending'
                      ORDER BY t.due_date''', (user_id, today.strftime("%Y-%m-%d")))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(chat_id, "✅ **Отлично! У вас нет просроченных работ.**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
        return
    text = "⚠️ **ПРОСРОЧЕННЫЕ РАБОТЫ**\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        due = datetime.strptime(t['due_date'], "%Y-%m-%d").date()
        days_overdue = (today - due).days
        text += f"\n{t['emoji']} **{t['name']}**\n  {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}\n  ⏰ Просрочено на {days_overdue} дн."
        kb.add(types.InlineKeyboardButton(f"⚙️ {t['title'][:25]}{'...' if len(t['title'])>25 else ''}", callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")

# ========== МЕНЮ И НАВИГАЦИЯ ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def menu_callback(call):
    bot.answer_callback_query(call.id)
    action = call.data.split("_")[1]
    if action == "subjects": show_subjects(call)
    elif action == "add": start_add_flow(call.from_user.id, call.message.chat.id, call)
    elif action == "calendar": show_calendar_menu(call)
    elif action == "reminders": show_reminders_menu(call)
    elif action == "settings": show_settings_menu(call)
    elif action == "stats": show_statistics(call)
    elif action == "my_tasks": show_my_tasks_filter(call)
    elif action == "quick": show_quick_actions(call)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def back_to_menu(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("Главное меню:", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())

# ========== РАЗДЕЛ 1: МОИ ПРЕДМЕТЫ ==========
def show_subjects(call):
    bot.answer_callback_query(call.id)
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ? ORDER BY name", (call.from_user.id,))
    subjects = cursor.fetchall()
    if not subjects:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"),
               types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
        bot.edit_message_text("📚 **У вас пока нет предметов**\n\nДобавьте первый, чтобы начать!", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")
        return

    kb = types.InlineKeyboardMarkup(row_width=1)
    for s in subjects:
        kb.add(types.InlineKeyboardButton(f"{s['emoji']} {s['name']}", callback_data=f"subject_{s['id']}"))
    kb.add(types.InlineKeyboardButton("➕ НОВЫЙ ПРЕДМЕТ", callback_data="add_subject"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.edit_message_text("📚 **ВАШИ ПРЕДМЕТЫ**", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("subject_"))
def show_subject_details(call):
    bot.answer_callback_query(call.id)
    subj_id = int(call.data.split("_")[1])
    cursor.execute("SELECT * FROM subjects WHERE id = ?", (subj_id,))
    subj = cursor.fetchone()
    if not subj: return

    cursor.execute('''SELECT * FROM tasks WHERE user_id = ? AND subject_id = ? ORDER BY due_date''', (call.from_user.id, subj_id))
    tasks = cursor.fetchall()
    upcoming, overdue, completed = [], [], []
    today = datetime.now().date()
    for t in tasks:
        due = datetime.strptime(t['due_date'], "%Y-%m-%d").date()
        if t['status'] == 'completed': completed.append(t)
        elif due < today: overdue.append(t)
        else: upcoming.append(t)

    text = f"{subj['emoji']} **{subj['name']}**\n\n"
    if overdue:
        text += "⚠️ **ПРОСРОЧЕННЫЕ:**\n"
        for t in overdue: text += f"  • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} — {format_date(t['due_date'])}\n"
        text += "\n"
    if upcoming:
        text += "📋 **ПРЕДСТОЯЩИЕ:**\n"
        for t in upcoming: text += f"  • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} — {format_date(t['due_date'])}\n"
        text += "\n"
    if completed:
        text += f"✅ **ВЫПОЛНЕННЫЕ ({len(completed)}):**\n"
        for t in completed[:3]: text += f"  • {t['title']} — {format_date(t['due_date'])}\n"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ РАБОТУ", callback_data=f"add_to_subject_{subj_id}"),
           types.InlineKeyboardButton("✏️ РЕДАКТИРОВАТЬ РАБОТЫ", callback_data=f"edit_subject_{subj_id}"),
           types.InlineKeyboardButton("🗑 УДАЛИТЬ ПРЕДМЕТ", callback_data=f"del_subject_{subj_id}"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("add_to_subject_"))
def add_to_subject(call):
    bot.answer_callback_query(call.id)
    subj_id = int(call.data.split("_")[3])
    user_temp[call.from_user.id] = {'subject_id': subj_id, 'preselected': True}
    fast_select_type(call, subj_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_subject_"))
def show_task_list_for_subject(call):
    bot.answer_callback_query(call.id)
    subj_id = int(call.data.split("_")[2])
    cursor.execute("SELECT * FROM subjects WHERE id = ?", (subj_id,))
    subj = cursor.fetchone()
    cursor.execute("SELECT * FROM tasks WHERE user_id = ? AND subject_id = ? AND status = 'pending'", (call.from_user.id, subj_id))
    tasks = cursor.fetchall()
    if not tasks:
        bot.edit_message_text(f"📚 **{subj['name']}**\n\nНет активных работ для редактирования.", call.message.chat.id, call.message.message_id)
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        kb.add(types.InlineKeyboardButton(f"{TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} ({format_date(t['due_date'])})", callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"subject_{subj_id}"))
    bot.edit_message_text(f"✏️ **РЕДАКТИРОВАНИЕ РАБОТ**\n{subj['emoji']} {subj['name']}", call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_subject_"))
def delete_subject_and_tasks(call):
    bot.answer_callback_query(call.id)
    subj_id = int(call.data.split("_")[2])
    cursor.execute("DELETE FROM tasks WHERE subject_id = ?", (subj_id,))
    cursor.execute("DELETE FROM subjects WHERE id = ?", (subj_id,))
    conn.commit()
    bot.edit_message_text("🗑 Предмет и все привязанные работы удалены.", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects")))

@bot.callback_query_handler(func=lambda call: call.data == "add_subject")
def add_subject_prompt(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📝 Введите название предмета:\nНапример: Математика, Физика, Программирование")
    bot.register_next_step_handler(msg, save_subject)

def save_subject(message):
    name = message.text.strip()
    if len(name) > 50:
        bot.send_message(message.chat.id, "❌ Название слишком длинное (макс. 50 символов)")
        return
    try:
        cursor.execute("INSERT INTO subjects (user_id, name) VALUES (?, ?)", (message.from_user.id, name))
        conn.commit()
        kb = types.InlineKeyboardMarkup(row_width=1).add(
            types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
            types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        bot.send_message(message.chat.id, f"✅ Предмет **{name}** добавлен!", reply_markup=kb, parse_mode="Markdown")
    except sqlite3.IntegrityError:
        bot.send_message(message.chat.id, f"❌ Предмет **{name}** уже существует!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔄 ПОПРОБОВАТЬ ЕЩЕ", callback_data="add_subject")))

# ========== РАЗДЕЛ 2: БЫСТРОЕ ДОБАВЛЕНИЕ ==========
user_temp = {}
def start_add_flow(user_id, chat_id, call=None):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ?", (user_id,))
    subjects = cursor.fetchall()
    if not subjects:
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"), types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
        msg = bot.edit_message_text("📚 **Сначала добавьте предмет!**", chat_id, call.message.message_id, reply_markup=kb, parse_mode="Markdown") if call else bot.send_message(chat_id, "📚 **Сначала добавьте предмет!**", reply_markup=kb, parse_mode="Markdown")
        return
    kb = types.InlineKeyboardMarkup(row_width=2)
    for s in subjects: kb.add(types.InlineKeyboardButton(f"{s['emoji']} {s['name']}", callback_data=f"fast_subject_{s['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    msg = bot.edit_message_text("📌 **Выберите предмет:**", chat_id, call.message.message_id, reply_markup=kb, parse_mode="Markdown") if call else bot.send_message(chat_id, "📌 **Выберите предмет:**", reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_subject_"))
def fast_select_subject(call):
    bot.answer_callback_query(call.id)
    user_temp[call.from_user.id] = {'subject_id': int(call.data.split("_")[2])}
    fast_select_type(call)

def fast_select_type(call, subj_id=None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for k, v in TASK_TYPE_EMOJIS.items(): kb.add(types.InlineKeyboardButton(v, callback_data=f"fast_type_{k}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_add" if not subj_id else f"subject_{subj_id}"))
    bot.edit_message_text("📋 **Выберите тип работы:**", call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_type_"))
def fast_select_type_confirm(call):
    bot.answer_callback_query(call.id)
    user_temp[call.from_user.id]['task_type'] = call.data.split("_")[2]
    kb = types.InlineKeyboardMarkup(row_width=5)
    for i in range(1, 6): kb.add(types.InlineKeyboardButton(str(i), callback_data=f"fast_qty_{i}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_add"))
    bot.edit_message_text("🔢 **Сколько работ добавить? (1-5)**", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_qty_"))
def fast_quantity(call):
    bot.answer_callback_query(call.id)
    user_temp[call.from_user.id].update({'quantity': int(call.data.split("_")[2]), 'tasks': [], 'current': 0})
    ask_task_title(call.from_user.id, call.message.chat.id)

def ask_task_title(user_id, chat_id):
    data = user_temp.get(user_id, {})
    if data.get('current', 0) >= data.get('quantity', 1):
        save_all_tasks(user_id, chat_id)
        return
    data['current'] = data['current'] + 1
    user_temp[user_id] = data
    msg = bot.send_message(chat_id, f"📝 **Работа №{data['current']} из {data['quantity']}**\n\nВведите название работы:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_task_title, user_id, chat_id)

def save_task_title(message, user_id, chat_id):
    data = user_temp.get(user_id, {})
    data['tasks'].append({'title': message.text.strip()})
    user_temp[user_id] = data
    msg = bot.send_message(chat_id, "📅 **Введите дату сдачи**\nФормат: ДД.ММ.ГГГГ", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_task_date_individual, user_id, chat_id)

def save_task_date_individual(message, user_id, chat_id):
    try:
        due = parse_date(message.text.strip())
    except:
        bot.send_message(chat_id, "❌ Неверный формат! Используйте ДД.ММ.ГГГГ")
        ask_task_title(user_id, chat_id)
        return
    data = user_temp.get(user_id, {})
    data['tasks'][data['current']-1]['due_date'] = due
    user_temp[user_id] = data
    ask_task_title(user_id, chat_id)

def save_all_tasks(user_id, chat_id):
    data = user_temp.get(user_id, {})
    saved = []
    for t in data['tasks']:
        cursor.execute("INSERT INTO tasks (user_id, subject_id, title, task_type, due_date) VALUES (?, ?, ?, ?, ?)",
                       (user_id, data['subject_id'], t['title'], data['task_type'], t['due_date']))
        saved.append(t['title'])
    conn.commit()
    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="menu_add"),
        types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
        types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    text = f"✅ **Добавлено {len(saved)} работ(а)!**\n" + "\n".join(f"  • {t}" for t in saved)
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")
    if user_id in user_temp: del user_temp[user_id]

# ========== РАЗДЕЛ 3: УМНЫЙ КАЛЕНДАРЬ ==========
def show_calendar_menu(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📆 НА МЕСЯЦ", callback_data="calendar_month"),
           types.InlineKeyboardButton("📋 НА НЕДЕЛЕ", callback_data="calendar_week"),
           types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="calendar_overdue"),
           types.InlineKeyboardButton("🔗 ЭКСПОРТ", callback_data="calendar_export"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.edit_message_text("📅 **УМНЫЙ КАЛЕНДАРЬ**\nВыберите режим просмотра:", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data in ["calendar_month", "calendar_week", "calendar_overdue", "calendar_export"])
def calendar_handler(call):
    bot.answer_callback_query(call.id)
    if call.data == "calendar_month":
        today = datetime.now().date()
        end = today + timedelta(days=30)
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = 'pending' ORDER BY t.due_date''',
                       (call.from_user.id, today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
        tasks = cursor.fetchall()
        if not tasks:
            bot.edit_message_text("📆 **НА МЕСЯЦ**\nНет работ на ближайшие 30 дней.", call.message.chat.id, call.message.message_id)
            return
        text = "📆 **РАБОТЫ НА МЕСЯЦ**\n"
        curr = None
        for t in tasks:
            if curr != t['due_date']: curr = t['due_date']; text += f"\n**{format_date(curr)}:**\n"
            text += f"  {t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}\n"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")), parse_mode="Markdown")
    elif call.data == "calendar_week":
        today = datetime.now().date()
        mon = today - timedelta(days=today.weekday())
        sun = mon + timedelta(days=6)
        show_weekly_tasks(call.message.chat.id, call.from_user.id, mon, sun)
        bot.answer_callback_query(call.id)
    elif call.data == "calendar_overdue":
        show_overdue_tasks(call.message.chat.id, call.from_user.id)
        bot.answer_callback_query(call.id)
    elif call.data == "calendar_export":
        bot.edit_message_text("🔗 ЭКСПОРТ В КАЛЕНДАРЬ\nФункция будет доступна в следующей версии.\nПока используйте: /today, /week", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")))

# ========== РАЗДЕЛ 4: УМНЫЕ НАПОМИНАНИЯ ==========
def show_reminders_menu(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⚙️ ШАБЛОНЫ НАПОМИНАНИЙ", callback_data="reminder_templates"),
           types.InlineKeyboardButton("🧠 СИСТЕМА УВЕДОМЛЕНИЙ", callback_data="reminder_aggregation"),
           types.InlineKeyboardButton("⏰ ВРЕМЯ ДАЙДЖЕСТА", callback_data="reminder_time"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.edit_message_text("🔔 **УМНЫЕ НАПОМИНАНИЯ**\n• Шаблоны — настройка уведомлений для разных типов работ\n• Система уведомлений — группировка сообщений в одно дайджест-сообщение", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("reminder_"))
def reminder_handler(call):
    bot.answer_callback_query(call.id)
    if call.data == "reminder_templates":
        bot.edit_message_text("⚙️ **ШАБЛОНЫ НАПОМИНАНИЙ**\n📝 Контрольная: за 3 дня, за 1 день\n🧪 Лабораторная: за 2 дня, за 1 день\n📚 ДЗ: за 1 день, за 3 часа (допустим)\n🎓 Экзамен: за 7 дней, за 3 дня, за 1 день\n📖 Курсовая: за 14 дней, за 7 дней, за 3 дня, за 1 день", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders")))
    elif call.data == "reminder_aggregation":
        s = get_user_settings(call.from_user.id)
        status = "✅ ВКЛЮЧЕНА" if s['aggregation_enabled'] else "❌ ВЫКЛЮЧЕНА"
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🔘 ВКЛ/ВЫКЛ СИСТЕМУ", callback_data="agg_toggle"),
            types.InlineKeyboardButton("⏰ ИЗМЕНИТЬ ВРЕМЯ ДАЙДЖЕСТА", callback_data="reminder_time"),
            types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders"))
        bot.edit_message_text(f"🧠 **СИСТЕМА УВЕДОМЛЕНИЙ**\nСтатус: {status}\nВремя дайджеста: {s['aggregation_hour']}:00\n\nПри включении вы получаете ОДНО групповое сообщение вместо множества отдельных.", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")
    elif call.data == "reminder_time":
        kb = types.InlineKeyboardMarkup(row_width=4)
        for h in range(24): kb.add(types.InlineKeyboardButton(f"{h:02}:00", callback_data=f"set_hour_{h}"))
        kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders"))
        bot.edit_message_text("⏰ **ВЫБЕРИТЕ ВРЕМЯ ВЕЧЕРНЕГО ДАЙДЖЕСТА** (00:00 - 23:00)", call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data in ["agg_toggle"] or call.data.startswith("set_hour_"))
def reminder_settings_action(call):
    bot.answer_callback_query(call.id)
    if call.data == "agg_toggle":
        s = get_user_settings(call.from_user.id)
        new_val = 0 if s['aggregation_enabled'] else 1
        cursor.execute("UPDATE user_settings SET aggregation_enabled = ? WHERE user_id = ?", (new_val, call.from_user.id))
        conn.commit()
        reminder_handler(call)
    elif call.data.startswith("set_hour_"):
        h = int(call.data.split("_")[2])
        cursor.execute("UPDATE user_settings SET aggregation_hour = ? WHERE user_id = ?", (h, call.from_user.id))
        conn.commit()
        reminder_handler(call)

# ========== РАЗДЕЛ 5: НАСТРОЙКИ ==========
def show_settings_menu(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="settings_profile"),
        types.InlineKeyboardButton("🔔 УВЕДОМЛЕНИЯ", callback_data="settings_notifications"),
        types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.edit_message_text("⚙️ **НАСТРОЙКИ**\nВыберите раздел:", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("settings_"))
def settings_handler(call):
    bot.answer_callback_query(call.id)
    if call.data == "settings_profile":
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (call.from_user.id,))
        u = cursor.fetchone()
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🌍 ИЗМЕНИТЬ ЧАСОВОЙ ПОЯС", callback_data="settings_timezone"),
            types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
        bot.edit_message_text(f"👤 **ПРОФИЛЬ**\nID: {u['user_id']}\nИмя: {u['first_name'] or '—'} {u['last_name'] or ''}\nUsername: @{u['username'] or '—'}\nЧасовой пояс: {u['timezone']}", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")
    elif call.data == "settings_timezone":
        kb = types.InlineKeyboardMarkup(row_width=2)
        for tz in ["UTC+3 (МСК)", "UTC+4 (Самара)", "UTC+5 (Екатеринбург)", "UTC+6 (Омск)", "UTC+7 (Новосибирск)", "UTC+8 (Иркутск)", "UTC+9 (Якутск)", "UTC+10 (Владивосток)"]:
            kb.add(types.InlineKeyboardButton(tz, callback_data=f"tz_{tz}"))
        kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="settings_profile"))
        bot.edit_message_text("🌍 **ВЫБЕРИТЕ ЧАСОВОЙ ПОЯС**", call.message.chat.id, call.message.message_id, reply_markup=kb)
    elif call.data.startswith("tz_"):
        cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (call.data.split("_")[1], call.from_user.id))
        conn.commit()
        bot.answer_callback_query(call.id, "✅ Часовой пояс обновлён")
        settings_handler(call)
    elif call.data == "settings_notifications":
        s = get_user_settings(call.from_user.id)
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
        bot.edit_message_text(f"🔔 **НАСТРОЙКИ УВЕДОМЛЕНИЙ**\n🕐 Тихие часы: {s.get('quiet_hours_start',23)}:00 - {s.get('quiet_hours_end',8)}:00\n🧠 Система уведомлений: {'✅ Вкл' if s['aggregation_enabled'] else '❌ Выкл'}\n⏰ Время дайджеста: {s['aggregation_hour']}:00", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

# ========== РАЗДЕЛ 6: СТАТИСТИКА ==========
def show_statistics(call):
    bot.answer_callback_query(call.id)
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'completed'", (call.from_user.id,))
    completed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'pending'", (call.from_user.id,))
    pending = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND due_date < ? AND status = 'pending'", (call.from_user.id, datetime.now().date().strftime("%Y-%m-%d")))
    overdue = cursor.fetchone()[0]
    total = completed + pending + overdue
    rate = (completed / total * 100) if total > 0 else 0

    text = f"📊 **СТАТИСТИКА**\n✅ Выполнено: {completed}\n⚠️ Просрочено: {overdue}\n📋 Активных: {pending}\n📈 Процент: {rate:.1f}%\n\n"
    cursor.execute('''SELECT s.name, s.emoji, COUNT(CASE WHEN t.status = 'completed' THEN 1 END) as comp, COUNT(*) as total
                      FROM subjects s LEFT JOIN tasks t ON t.subject_id = s.id
                      WHERE s.user_id = ? GROUP BY s.id''', (call.from_user.id,))
    by_subj = cursor.fetchall()
    if by_subj:
        text += "**ПО ПРЕДМЕТАМ:**\n"
        for s in by_subj: r = (s['comp'] / s['total'] * 100) if s['total'] > 0 else 0; text += f"  {s['emoji']} {s['name']}: {r:.0f}% ({s['comp']}/{s['total']})\n"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")), parse_mode="Markdown")

# ========== РАЗДЕЛ 7: МОИ РАБОТЫ ==========
def show_my_tasks_filter(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🟢 ОЖИДАЕТ", callback_data="filter_status_pending"),
           types.InlineKeyboardButton("✅ ВЫПОЛНЕННО", callback_data="filter_status_completed"))
    for k in TASK_TYPE_EMOJIS.keys():
        kb.add(types.InlineKeyboardButton(TASK_TYPE_EMOJIS[k], callback_data=f"filter_type_{k}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.edit_message_text("📂 **МОИ РАБОТЫ**\nВыберите фильтр просмотра:", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("filter_"))
def filter_my_tasks(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split("_")
    f_type, f_val = parts[1], parts[2]
    base_q = "SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ?"
    params = [call.from_user.id]
    if f_type == "status": base_q += " AND t.status = ?"
    elif f_type == "type": base_q += " AND t.task_type = ?"
    params.append(f_val)
    base_q += " ORDER BY t.due_date"
    cursor.execute(base_q, params)
    tasks = cursor.fetchall()
    if not tasks:
        bot.edit_message_text("📂 **ФИЛЬТР**\nРабот по выбранному критерию не найдено.", call.message.chat.id, call.message.message_id, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_my_tasks")))
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        kb.add(types.InlineKeyboardButton(f"{t['emoji']} {t['name']} • {t['title']} ({format_date(t['due_date'])})", callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_my_tasks"))
    bot.edit_message_text(f"📂 **РЕЗУЛЬТАТ ФИЛЬТРА**\nНайдено: {len(tasks)}", call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("my_task_"))
def task_action_menu(call):
    bot.answer_callback_query(call.id)
    t_id = int(call.data.split("_")[2])
    cursor.execute("SELECT * FROM tasks WHERE id = ?", (t_id,))
    t = cursor.fetchone()
    if not t: return
    kb = types.InlineKeyboardMarkup(row_width=2)
    if t['status'] == 'pending':
        kb.add(types.InlineKeyboardButton("✅ ОТМЕТИТЬ ВЫПОЛНЕННЫМ", callback_data=f"task_complete_{t_id}"),
               types.InlineKeyboardButton("📅 ПЕРЕНЕСТИ ДАТУ", callback_data=f"task_postpone_{t_id}"),
               types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"task_delete_{t_id}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.edit_message_text(f"📝 **{t['title']}**\n📚 {t.get('name', 'Предмет')} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')}\n📅 Сдача: {format_date(t['due_date'])}\nСтатус: {t['status']}", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("task_"))
def handle_task_action(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split("_")
    action, t_id = parts[1], int(parts[2])
    if action == "complete":
        cursor.execute("UPDATE tasks SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?", (t_id,))
        conn.commit()
        bot.answer_callback_query(call.id, "✅ Отмечено как выполненное!")
    elif action == "delete":
        cursor.execute("DELETE FROM tasks WHERE id = ?", (t_id,))
        conn.commit()
        bot.answer_callback_query(call.id, "🗑 Работа удалена!")
    elif action == "postpone":
        msg = bot.send_message(call.message.chat.id, "📅 Введите новую дату сдачи (ДД.ММ.ГГГГ):")
        bot.register_next_step_handler(msg, save_new_task_date, call.from_user.id, call.message.chat.id, t_id)
        return
    show_my_tasks_filter(call)

def save_new_task_date(message, user_id, chat_id, task_id):
    try:
        new_date = parse_date(message.text.strip())
        cursor.execute("UPDATE tasks SET due_date = ? WHERE id = ?", (new_date, task_id))
        conn.commit()
        bot.send_message(chat_id, f"✅ Дата перенесена на {format_date(new_date)}")
    except:
        bot.send_message(chat_id, "❌ Неверный формат! Попробуйте снова.")
        msg = bot.send_message(chat_id, "📅 Введите новую дату сдачи (ДД.ММ.ГГГГ):")
        bot.register_next_step_handler(msg, save_new_task_date, user_id, chat_id, task_id)

# ========== РАЗДЕЛ 8: БЫСТРЫЕ ДЕЙСТВИЯ ==========
def show_quick_actions(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📅 СЕГОДНЯ", callback_data="quick_today"),
           types.InlineKeyboardButton("📅 ЗАВТРА", callback_data="quick_tomorrow"),
           types.InlineKeyboardButton("📅 НЕДЕЛЯ", callback_data="quick_week"),
           types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="quick_overdue"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.edit_message_text("⚡ **БЫСТРЫЕ ДЕЙСТВИЯ**", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("quick_"))
def quick_action_handler(call):
    bot.answer_callback_query(call.id)
    action = call.data.split("_")[1]
    if action == "today": show_tasks_for_date(call.message.chat.id, call.from_user.id, datetime.now().date(), "СЕГОДНЯ")
    elif action == "tomorrow": show_tasks_for_date(call.message.chat.id, call.from_user.id, (datetime.now() + timedelta(days=1)).date(), "ЗАВТРА")
    elif action == "week":
        today = datetime.now().date()
        mon = today - timedelta(days=today.weekday())
        sun = mon + timedelta(days=6)
        show_weekly_tasks(call.message.chat.id, call.from_user.id, mon, sun)
    elif action == "overdue": show_overdue_tasks(call.message.chat.id, call.from_user.id)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("🚀 Бот запущен и готов к работе!")
    bot.infinity_polling(skip_pending=True)
