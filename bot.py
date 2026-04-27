import telebot
from telebot import types
import sqlite3
from datetime import datetime, timedelta
import os
import logging

# ========== НАСТРОЙКА ==========
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    logging.error("Ошибка: переменная BOT_TOKEN не установлена!")
    exit(1)

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('university_bot.db', check_same_thread=False)
conn.row_factory = sqlite3.Row  # Безопасный доступ к колонкам по имени
cursor = conn.cursor()

# Инициализация таблиц
cursor.executescript('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    university TEXT DEFAULT '',
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
    due_time TEXT,
    status TEXT DEFAULT 'pending',
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
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

# Добавление колонки university, если БД старая
try:
    cursor.execute("ALTER TABLE users ADD COLUMN university TEXT DEFAULT ''")
    conn.commit()
except:
    pass  # Колонка уже существует

conn.commit()

# ========== ЭМОДЗИ И КОНСТАНТЫ ==========
TASK_TYPE_EMOJIS = {
    "control": "📝 Контрольная",
    "lab": "🧪 Лабораторная",
    "homework": "📚 Домашнее задание",
    "exam": "🎓 Экзамен",
    "coursework": "📖 Курсовая/Диплом",
    "other": "📌 Другое"
}

user_actions = {}  # Для хранения состояний (например, переноса даты)
user_temp = {}     # Для добавления работ

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
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
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
    return datetime.strptime(date_str.strip(), "%d.%m.%Y").strftime("%Y-%m-%d")

def safe_edit(call, text, reply_markup=None, parse_mode="Markdown"):
    """Безопасное редактирование сообщения. Если не получается - отправит новое."""
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup, parse_mode=parse_mode)
    except:
        bot.send_message(call.message.chat.id, text, reply_markup=reply_markup, parse_mode=parse_mode)

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
        types.InlineKeyboardButton("⚡ БЫСТРЫЙ ПРОСМОТР", callback_data="menu_quick")
    )
    return keyboard

def nav_buttons(section_callback="back_to_menu", main_callback="back_to_menu"):
    """Генерирует стандартные кнопки навигации."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    if section_callback:
        kb.add(types.InlineKeyboardButton("◀️ В РАЗДЕЛ", callback_data=section_callback))
    kb.add(types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    return kb

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

@bot.message_handler(commands=['today', 'tomorrow', 'week', 'overdue', 'add'])
def generic_command_handler(message):
    cmd = message.text[1:].lower()
    if cmd == 'today':
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending' ORDER BY t.due_date''', 
                          (message.from_user.id, today_str))
        tasks = cursor.fetchall()
        if not tasks: return bot.send_message(message.chat.id, f"📅 СЕГОДНЯ ({format_date(today_str)})\nНет запланированных работ.")
        text = f"📅 СЕГОДНЯ ({format_date(today_str)})\n\n" + "\n".join([f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}" for t in tasks])
        bot.send_message(message.chat.id, text, parse_mode="Markdown")
    elif cmd == 'tomorrow':
        tom_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending' ORDER BY t.due_date''', 
                          (message.from_user.id, tom_str))
        tasks = cursor.fetchall()
        if not tasks: return bot.send_message(message.chat.id, f"📅 ЗАВТРА ({format_date(tom_str)})\nНет запланированных работ.")
        text = f"📅 ЗАВТРА ({format_date(tom_str)})\n\n" + "\n".join([f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}" for t in tasks])
        bot.send_message(message.chat.id, text, parse_mode="Markdown")
    elif cmd == 'week':
        today = datetime.now().date()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = 'pending' ORDER BY t.due_date''', 
                          (message.from_user.id, monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")))
        tasks = cursor.fetchall()
        if not tasks: return bot.send_message(message.chat.id, f"📅 НА НЕДЕЛЕ ({format_date(monday.strftime('%Y-%m-%d'))} - {format_date(sunday.strftime('%Y-%m-%d'))})\nНет работ.")
        text = f"📅 НА НЕДЕЛЕ ({format_date(monday.strftime('%Y-%m-%d'))} - {format_date(sunday.strftime('%Y-%m-%d'))})\n\n" + "\n".join([f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} — {format_date(t['due_date'])}" for t in tasks])
        bot.send_message(message.chat.id, text, parse_mode="Markdown")
    elif cmd == 'overdue':
        show_overdue_tasks(message.chat.id, message.from_user.id, callback_back="menu_quick")
    elif cmd == 'add':
        start_add_flow(message.from_user.id, message.chat.id)

# ========== УНИВЕРСАЛЬНЫЕ ДЕЙСТВИЯ С ЗАДАЧАМИ ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("my_task_"))
def task_action_menu(call):
    bot.answer_callback_query(call.id)
    task_id = int(call.data.split("_")[2])
    cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.id = ?''', (task_id,))
    t = cursor.fetchone()
    if not t: return bot.answer_callback_query(call.id, "Задача не найдена")
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    if t['status'] == 'pending':
        kb.add(types.InlineKeyboardButton("✅ ВЫПОЛНЕНА", callback_data=f"task_complete_{task_id}"),
               types.InlineKeyboardButton("📅 ПЕРЕНЕСТИ ДАТУ", callback_data=f"task_postpone_{task_id}"),
               types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"task_delete_{task_id}"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"),
           types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    safe_edit(call, f"📝 **{t['title']}**\n📚 {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')}\n📅 Сдача: {format_date(t['due_date'])}\nСтатус: {t['status']}", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("task_complete_"))
def task_complete(call):
    bot.answer_callback_query(call.id, "✅ Отмечено как выполненное!")
    t_id = int(call.data.split("_")[2])
    cursor.execute("UPDATE tasks SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?", (t_id,))
    conn.commit()
    bot.send_message(call.message.chat.id, "✅ Работа успешно отмечена как выполненная.", reply_markup=nav_buttons("menu_my_tasks"))

@bot.callback_query_handler(func=lambda call: call.data.startswith("task_postpone_"))
def task_postpone_start(call):
    bot.answer_callback_query(call.id)
    t_id = int(call.data.split("_")[2])
    user_actions[call.from_user.id] = {'action': 'postpone', 'task_id': t_id, 'section': 'my_tasks'}
    msg = bot.send_message(call.message.chat.id, "📅 Введите новую дату сдачи (ДД.ММ.ГГГГ):")
    bot.register_next_step_handler(msg, process_postpone_date, call.from_user.id)

def process_postpone_date(message, user_id):
    act = user_actions.get(user_id)
    if not act: return
    try:
        new_date = parse_date(message.text)
        cursor.execute("UPDATE tasks SET due_date=? WHERE id=?", (new_date, act['task_id']))
        conn.commit()
        section = act.get('section', 'my_tasks')
        bot.send_message(message.chat.id, f"✅ Дата успешно перенесена на {format_date(new_date)}.", 
                         reply_markup=nav_buttons(f"menu_{section}" if section != 'quick' else "menu_quick"))
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат. Используйте ДД.ММ.ГГГГ")
        msg = bot.send_message(message.chat.id, "📅 Введите новую дату сдачи (ДД.ММ.ГГГГ):")
        bot.register_next_step_handler(msg, process_postpone_date, user_id)
    finally:
        if user_id in user_actions: del user_actions[user_id]

@bot.callback_query_handler(func=lambda call: call.data.startswith("task_delete_"))
def task_delete(call):
    bot.answer_callback_query(call.id, "🗑 Задача удалена!")
    t_id = int(call.data.split("_")[2])
    cursor.execute("DELETE FROM tasks WHERE id=?", (t_id,))
    conn.commit()
    bot.send_message(call.message.chat.id, "🗑 Работа успешно удалена.", reply_markup=nav_buttons("menu_my_tasks"))

def show_overdue_tasks(chat_id, user_id, callback_back="back_to_menu"):
    today = datetime.now().date().strftime("%Y-%m-%d")
    cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                      WHERE t.user_id = ? AND t.due_date < ? AND t.status = 'pending' ORDER BY t.due_date''', (user_id, today))
    tasks = cursor.fetchall()
    if not tasks:
        return bot.send_message(chat_id, "✅ Отлично! У вас нет просроченных работ.", reply_markup=nav_buttons(callback_back))
    text = "⚠️ ПРОСРОЧЕННЫЕ РАБОТЫ:\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        due = datetime.strptime(t['due_date'], "%Y-%m-%d").date()
        days = (datetime.now().date() - due).days
        text += f"\n{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}\n  ⏰ Просрочено на {days} дн. ({format_date(t['due_date'])})"
        kb.add(types.InlineKeyboardButton(f"⚙️ {t['title'][:25]}", callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=callback_back))
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")

# ========== МЕНЮ И НАВИГАЦИЯ ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def menu_router(call):
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
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ? ORDER BY name", (call.from_user.id,))
    subs = cursor.fetchall()
    if not subs:
        return safe_edit(call, "📚 У вас пока нет предметов.\nДобавьте первый, чтобы начать!", 
                         types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data="add_subject"), types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")))
    kb = types.InlineKeyboardMarkup(row_width=1)
    for s in subs:
        kb.add(types.InlineKeyboardButton(f"{s['emoji']} {s['name']}", callback_data=f"subject_{s['id']}"))
    kb.add(types.InlineKeyboardButton("➕ НОВЫЙ ПРЕДМЕТ", callback_data="add_subject"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "📚 ВАШИ ПРЕДМЕТЫ", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("subject_"))
def show_subject_details(call):
    bot.answer_callback_query(call.id)
    sid = int(call.data.split("_")[1])
    cursor.execute("SELECT * FROM subjects WHERE id=?", (sid,))
    subj = cursor.fetchone()
    cursor.execute('''SELECT * FROM tasks WHERE user_id=? AND subject_id=? AND status='pending' ORDER BY due_date''', (call.from_user.id, sid))
    tasks = cursor.fetchall()
    today = datetime.now().date()
    upcoming = [t for t in tasks if datetime.strptime(t['due_date'], "%Y-%m-%d").date() >= today]
    overdue = [t for t in tasks if datetime.strptime(t['due_date'], "%Y-%m-%d").date() < today]

    text = f"{subj['emoji']} **{subj['name']}**\n\n"
    if overdue: text += "⚠️ ПРОСРОЧЕННЫЕ:\n" + "".join([f"  • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']} — {format_date(t['due_date'])}\n" for t in overdue]) + "\n"
    if upcoming: text += "📋 ПРЕДСТОЯЩИЕ:\n" + "".join([f"  • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']} — {format_date(t['due_date'])}\n" for t in upcoming]) + "\n"
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ РАБОТУ", callback_data=f"add_to_subject_{sid}"),
           types.InlineKeyboardButton("✏️ РЕДАКТИРОВАТЬ РАБОТЫ", callback_data=f"edit_subject_{sid}"),
           types.InlineKeyboardButton("🗑 УДАЛИТЬ ПРЕДМЕТ", callback_data=f"del_subject_{sid}"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
    safe_edit(call, text, kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_subject_"))
def edit_subject_tasks(call):
    bot.answer_callback_query(call.id)
    sid = int(call.data.split("_")[2])
    cursor.execute("SELECT name FROM subjects WHERE id=?", (sid,))
    subj_name = cursor.fetchone()[0]
    cursor.execute('''SELECT t.*, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                      WHERE t.user_id=? AND t.subject_id=? AND t.status='pending' ORDER BY t.due_date''', (call.from_user.id, sid))
    tasks = cursor.fetchall()
    if not tasks:
        return safe_edit(call, f"✏️ РАБОТЫ ПО ПРЕДМЕТУ: **{subj_name}**\nНет активных работ для редактирования.", 
                         types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"subject_{sid}")))
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        kb.add(types.InlineKeyboardButton(f"{TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']} ({format_date(t['due_date'])})", callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"subject_{sid}"))
    safe_edit(call, f"✏️ РЕДАКТИРОВАНИЕ РАБОТ: **{subj_name}**\nВыберите работу:", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("add_to_subject_"))
def add_to_subject(call):
    bot.answer_callback_query(call.id)
    sid = int(call.data.split("_")[3])
    user_temp[call.from_user.id] = {'subject_id': sid, 'preselected': True}
    start_add_flow(call.from_user.id, call.message.chat.id, call)

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_subject_"))
def delete_subject(call):
    bot.answer_callback_query(call.id, "🗑 Предмет и все работы удалены!")
    sid = int(call.data.split("_")[2])
    cursor.execute("DELETE FROM tasks WHERE subject_id=?", (sid,))
    cursor.execute("DELETE FROM subjects WHERE id=?", (sid,))
    conn.commit()
    safe_edit(call, "🗑 Предмет и все привязанные работы успешно удалены.", 
              types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ К ПРЕДМЕТАМ", callback_data="menu_subjects")))

@bot.callback_query_handler(func=lambda call: call.data == "add_subject")
def add_subject_prompt(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📝 Введите название предмета:\nНапример: Математика, Физика")
    bot.register_next_step_handler(msg, save_subject)

def save_subject(message):
    name = message.text.strip()
    if len(name) > 50: return bot.send_message(message.chat.id, "❌ Название слишком длинное.")
    try:
        cursor.execute("INSERT INTO subjects (user_id, name) VALUES (?, ?)", (message.from_user.id, name))
        conn.commit()
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"), types.InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        bot.send_message(message.chat.id, f"✅ Предмет **{name}** добавлен!", reply_markup=kb, parse_mode="Markdown")
    except sqlite3.IntegrityError:
        bot.send_message(message.chat.id, f"❌ Предмет **{name}** уже существует!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔄 ПОПРОБОВАТЬ ЕЩЁ", callback_data="add_subject")))

# ========== РАЗДЕЛ 2: БЫСТРОЕ ДОБАВЛЕНИЕ ==========
def start_add_flow(user_id, chat_id, call=None):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ?", (user_id,))
    subjects = cursor.fetchall()
    if not subjects:
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"), types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
        msg = bot.edit_message_text("📚 Сначала добавьте предмет!", chat_id, call.message.message_id, reply_markup=kb, parse_mode="Markdown") if call else bot.send_message(chat_id, "📚 Сначала добавьте предмет!", reply_markup=kb, parse_mode="Markdown")
        return
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    for s in subjects: kb.add(types.InlineKeyboardButton(f"{s['emoji']} {s['name']}", callback_data=f"fast_subject_{s['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    msg = bot.edit_message_text("📌 Выберите предмет:", chat_id, call.message.message_id, reply_markup=kb, parse_mode="Markdown") if call else bot.send_message(chat_id, "📌 Выберите предмет:", reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_subject_"))
def fast_select_subject(call):
    bot.answer_callback_query(call.id)
    user_temp[call.from_user.id] = {'subject_id': int(call.data.split("_")[2])}
    kb = types.InlineKeyboardMarkup(row_width=2)
    for k, v in TASK_TYPE_EMOJIS.items(): kb.add(types.InlineKeyboardButton(v, callback_data=f"fast_type_{k}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_add"))
    bot.edit_message_text("📋 Выберите тип работы:", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_type_"))
def fast_select_type(call):
    bot.answer_callback_query(call.id)
    user_temp[call.from_user.id]['task_type'] = call.data.split("_")[2]
    kb = types.InlineKeyboardMarkup(row_width=5)
    for i in range(1, 6): kb.add(types.InlineKeyboardButton(str(i), callback_data=f"fast_qty_{i}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_add"))
    bot.edit_message_text("🔢 Сколько работ добавить? (1-5)", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

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
    data['current'] += 1
    user_temp[user_id] = data
    msg = bot.send_message(chat_id, f"📝 Работа №{data['current']} из {data['quantity']}\nВведите название работы:")
    bot.register_next_step_handler(msg, save_task_title, user_id, chat_id)

def save_task_title(message, user_id, chat_id):
    data = user_temp.get(user_id, {})
    data['tasks'].append({'title': message.text.strip()})
    user_temp[user_id] = data
    msg = bot.send_message(chat_id, "📅 Введите дату сдачи (ДД.ММ.ГГГГ):")
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
        types.InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЁ", callback_data="menu_add"),
        types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
        types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    text = f"✅ Добавлено {len(saved)} работ(а)!\n" + "\n".join(f"  • {t}" for t in saved)
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
    safe_edit(call, "📅 УМНЫЙ КАЛЕНДАРЬ", kb)

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
        if not tasks: return safe_edit(call, "📆 НА МЕСЯЦ (+30 дней)\nНет работ на ближайшие 30 дней.")
        text = "📆 РАБОТЫ НА МЕСЯЦ:\n"
        curr = None
        for t in tasks:
            if curr != t['due_date']: curr = t['due_date']; text += f"\n**{format_date(curr)}:**\n"
            text += f"  {t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}\n"
        safe_edit(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")))
    elif call.data == "calendar_week":
        today = datetime.now().date()
        mon = today - timedelta(days=today.weekday())
        sun = mon + timedelta(days=6)
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = 'pending' ORDER BY t.due_date''',
                       (call.from_user.id, mon.strftime("%Y-%m-%d"), sun.strftime("%Y-%m-%d")))
        tasks = cursor.fetchall()
        if not tasks: return safe_edit(call, f"📋 НА НЕДЕЛЕ ({format_date(mon.strftime('%Y-%m-%d'))} - {format_date(sun.strftime('%Y-%m-%d'))})\nНет работ.")
        text = f"📋 НА НЕДЕЛЕ ({format_date(mon.strftime('%Y-%m-%d'))} - {format_date(sun.strftime('%Y-%m-%d'))})\n\n"
        for t in tasks: text += f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} — {format_date(t['due_date'])}\n"
        safe_edit(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")))
    elif call.data == "calendar_overdue":
        show_overdue_tasks(call.message.chat.id, call.from_user.id)
    elif call.data == "calendar_export":
        safe_edit(call, "🔗 ЭКСПОРТ В КАЛЕНДАРЬ будет доступен в следующем обновлении.\nИспользуйте: /today, /week, /overdue", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")))

# ========== РАЗДЕЛ 4: УМНЫЕ НАПОМИНАНИЯ ==========
def show_reminders_menu(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⚙️ ШАБЛОНЫ НАПОМИНАНИЙ", callback_data="reminder_templates"),
           types.InlineKeyboardButton("🧠 СИСТЕМА УВЕДОМЛЕНИЙ", callback_data="reminder_system"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "🔔 УМНЫЕ НАПОМИНАНИЯ", kb)

@bot.callback_query_handler(func=lambda call: call.data == "reminder_templates")
def reminder_templates(call):
    bot.answer_callback_query(call.id)
    text = "⚙️ ШАБЛОНЫ НАПОМИНАНИЙ ПО УМОЛЧАНИЮ:\n• 📝 Контрольная: за 3 дня, за 1 день\n• 🧪 Лабораторная: за 2 дня, за 1 день\n• 📚 ДЗ: за 1 день, за 3 часа\n• 🎓 Экзамен: за 7 дней, за 3 дня, за 1 день"
    safe_edit(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders")))

@bot.callback_query_handler(func=lambda call: call.data == "reminder_system")
def reminder_system(call):
    bot.answer_callback_query(call.id)
    s = get_user_settings(call.from_user.id)
    status = "✅ ВКЛЮЧЕНА" if s['aggregation_enabled'] else "❌ ВЫКЛЮЧЕНА"
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🔘 ВКЛ/ВЫКЛ СИСТЕМУ", callback_data="agg_toggle"),
           types.InlineKeyboardButton("⏰ ИЗМЕНИТЬ ВРЕМЯ", callback_data="reminder_change_time"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders"))
    text = f"🧠 СИСТЕМА УВЕДОМЛЕНИЙ\nСтатус: {status}\nТекущее время дайджеста: {s['aggregation_hour']}:00"
    safe_edit(call, text, kb)

@bot.callback_query_handler(func=lambda call: call.data == "agg_toggle")
def toggle_aggregation(call):
    bot.answer_callback_query(call.id)
    s = get_user_settings(call.from_user.id)
    new_val = 0 if s['aggregation_enabled'] else 1
    cursor.execute("UPDATE user_settings SET aggregation_enabled = ? WHERE user_id = ?", (new_val, call.from_user.id))
    conn.commit()
    bot.answer_callback_query(call.id, f"Система {'включена' if new_val else 'выключена'}!", show_alert=True)
    reminder_system(call)

@bot.callback_query_handler(func=lambda call: call.data == "reminder_change_time")
def change_reminder_time(call):
    bot.answer_callback_query(call.id)
    s = get_user_settings(call.from_user.id)
    current_h = s['aggregation_hour']
    kb = types.InlineKeyboardMarkup(row_width=4)
    for h in range(24):
        txt = f"✅ {h:02}:00" if h == current_h else f"{h:02}:00"
        kb.add(types.InlineKeyboardButton(txt, callback_data=f"set_hour_{h}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="reminder_system"))
    bot.edit_message_text("⏰ ВЫБЕРИТЕ ВРЕМЯ ДАЙДЖЕСТА (00:00 - 23:00)", call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_hour_"))
def set_agg_hour(call):
    bot.answer_callback_query(call.id)
    h = int(call.data.split("_")[2])
    cursor.execute("UPDATE user_settings SET aggregation_hour = ? WHERE user_id = ?", (h, call.from_user.id))
    conn.commit()
    bot.answer_callback_query(call.id, f"⏰ Время установлено на {h:02}:00", show_alert=True)
    reminder_system(call)

# ========== РАЗДЕЛ 5: НАСТРОЙКИ ==========
def show_settings_menu(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="settings_profile"),
           types.InlineKeyboardButton("🔔 УВЕДОМЛЕНИЯ", callback_data="settings_notifications"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "⚙️ НАСТРОЙКИ", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("settings_"))
def settings_handler(call):
    bot.answer_callback_query(call.id)
    if call.data == "settings_profile":
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (call.from_user.id,))
        u = cursor.fetchone()
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🏛️ ИЗМЕНИТЬ ВУЗ", callback_data="settings_university"),
            types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
        safe_edit(call, f"👤 ПРОФИЛЬ:\nID: {u['user_id']}\nИмя: {u['first_name'] or '—'} {u['last_name'] or ''}\n🏛️ ВУЗ: {u['university'] or 'Не указан'}", kb)
    elif call.data == "settings_university":
        msg = bot.send_message(call.message.chat.id, "🏛️ Введите название вашего учебного заведения:")
        bot.register_next_step_handler(msg, save_university, call.from_user.id)
    elif call.data == "settings_notifications":
        s = get_user_settings(call.from_user.id)
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
        safe_edit(call, f"🔔 НАСТРОЙКИ УВЕДОМЛЕНИЙ:\n🧠 Система уведомлений: {'✅ Вкл' if s['aggregation_enabled'] else '❌ Выкл'}\n⏰ Время дайджеста: {s['aggregation_hour']}:00\n\nТихие часы: {s.get('quiet_hours_start',23)}:00 - {s.get('quiet_hours_end',8)}:00", kb)

def save_university(message, user_id):
    uni = message.text.strip()
    if len(uni) > 100: return bot.send_message(message.chat.id, "❌ Название слишком длинное.")
    cursor.execute("UPDATE users SET university = ? WHERE user_id = ?", (uni, user_id))
    conn.commit()
    bot.send_message(message.chat.id, f"✅ ВУЗ изменён на: **{uni}**", reply_markup=nav_buttons("menu_settings"), parse_mode="Markdown")

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

    text = f"📊 СТАТИСТИКА:\n✅ Выполнено: {completed}\n⚠️ Просрочено: {overdue}\n📋 Активных: {pending}\n📈 Процент: {rate:.1f}%\n\n"
    cursor.execute('''SELECT s.name, s.emoji, COUNT(CASE WHEN t.status = 'completed' THEN 1 END) as comp, COUNT(*) as total
                      FROM subjects s LEFT JOIN tasks t ON t.subject_id = s.id WHERE s.user_id = ? GROUP BY s.id''', (call.from_user.id,))
    by_subj = cursor.fetchall()
    if by_subj:
        text += "**ПО ПРЕДМЕТАМ:**\n"
        for s in by_subj: r = (s['comp'] / s['total'] * 100) if s['total'] > 0 else 0; text += f"  {s['emoji']} {s['name']}: {r:.0f}% ({s['comp']}/{s['total']})\n"
    safe_edit(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")))

# ========== РАЗДЕЛ 7: МОИ РАБОТЫ (ПОЛНОСТЬЮ ИСПРАВЛЕН) ==========
def show_my_tasks_main(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📋 ПО ТИПАМ РАБОТ", callback_data="tasks_by_type"))
    kb.add(types.InlineKeyboardButton("📊 ПО СТАТУСУ (ВЫПОЛНЕННЫ/ПРОСРОЧЕНЫ)", callback_data="tasks_by_status"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "📂 **МОИ РАБОТЫ**\nВыберите фильтр просмотра:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "tasks_by_type")
def show_type_filter(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    for k, v in TASK_TYPE_EMOJIS.items():
        kb.add(types.InlineKeyboardButton(v, callback_data=f"filter_type_{k}"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"))
    safe_edit(call, "📋 **ВЫБЕРИТЕ ТИП РАБОТЫ**", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("filter_type_"))
def filter_tasks_by_type(call):
    bot.answer_callback_query(call.id)
    task_type = call.data.split("_")[2]
    cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                      WHERE t.user_id = ? AND t.task_type = ? AND t.status = 'pending' ORDER BY t.due_date''',
                   (call.from_user.id, task_type))
    tasks = cursor.fetchall()
    show_task_list(call, tasks, "menu_my_tasks", f"📋 {TASK_TYPE_EMOJIS.get(task_type, '📌')}")

@bot.callback_query_handler(func=lambda c: c.data == "tasks_by_status")
def show_status_filter(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("✅ ВЫПОЛНЕННЫЕ", callback_data="filter_status_completed"))
    kb.add(types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="filter_status_overdue"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"))
    safe_edit(call, "📊 **ВЫБЕРИТЕ СТАТУС**", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("filter_status_"))
def filter_tasks_by_status(call):
    bot.answer_callback_query(call.id)
    status_type = call.data.split("_")[2]
    today = datetime.now().date().strftime("%Y-%m-%d")
    if status_type == "completed":
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.status = 'completed' ORDER BY t.completed_at DESC''',
                       (call.from_user.id,))
        title = "✅ ВЫПОЛНЕННЫЕ"
    elif status_type == "overdue":
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date < ? AND t.status = 'pending' ORDER BY t.due_date''',
                       (call.from_user.id, today))
        title = "⚠️ ПРОСРОЧЕННЫЕ"
    else:
        return
    tasks = cursor.fetchall()
    show_task_list(call, tasks, "menu_my_tasks", title)

def show_task_list(call, tasks, back_callback, title):
    if not tasks:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"),
               types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        safe_edit(call, f"{title}\nРабот не найдено.", kb)
        return

    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        # Формат строго как просили: предмет • название (дата)
        label = f"{t['emoji']} {t['name']} • {t['title']} ({format_date(t['due_date'])})"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"),
           types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    safe_edit(call, f"{title} ({len(tasks)} работ):", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("my_task_"))
def task_action_menu(call):
    bot.answer_callback_query(call.id)
    task_id = int(call.data.split("_")[2])
    cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.id = ?''', (task_id,))
    t = cursor.fetchone()
    if not t: return bot.answer_callback_query(call.id, "Задача не найдена")

    kb = types.InlineKeyboardMarkup(row_width=1)
    if t['status'] == 'pending':
        kb.add(types.InlineKeyboardButton("✅ ОТМЕТИТЬ ВЫПОЛНЕННОЙ", callback_data=f"task_complete_{task_id}"),
               types.InlineKeyboardButton("📅 ПЕРЕНЕСТИ ДАТУ", callback_data=f"task_postpone_{task_id}"),
               types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"task_delete_{task_id}"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"),
           types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    safe_edit(call, f"📝 **{t['title']}**\n📚 {t['name']}\n📅 Сдача: {format_date(t['due_date'])}\nСтатус: {t['status']}", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("task_complete_"))
def task_complete(call):
    bot.answer_callback_query(call.id, "✅ Отмечено как выполненное!")
    t_id = int(call.data.split("_")[2])
    cursor.execute("UPDATE tasks SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?", (t_id,))
    conn.commit()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"))
    kb.add(types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    bot.send_message(call.message.chat.id, "✅ Работа успешно отмечена как выполненная.", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("task_postpone_"))
def task_postpone_start(call):
    bot.answer_callback_query(call.id)
    t_id = int(call.data.split("_")[2])
    user_actions[call.from_user.id] = {'action': 'postpone', 'task_id': t_id, 'section': 'my_tasks'}
    msg = bot.send_message(call.message.chat.id, "📅 Введите новую дату сдачи (ДД.ММ.ГГГГ):")
    bot.register_next_step_handler(msg, process_postpone_date, call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("task_delete_"))
def task_delete(call):
    bot.answer_callback_query(call.id, "🗑 Задача удалена!")
    t_id = int(call.data.split("_")[2])
    cursor.execute("DELETE FROM tasks WHERE id=?", (t_id,))
    conn.commit()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"))
    kb.add(types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    bot.send_message(call.message.chat.id, "🗑 Работа успешно удалена.", reply_markup=kb)

def process_postpone_date(message, user_id):
    act = user_actions.get(user_id)
    if not act: return
    try:
        new_date = parse_date(message.text)
        cursor.execute("UPDATE tasks SET due_date=? WHERE id=?", (new_date, act['task_id']))
        conn.commit()
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"))
        kb.add(types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        bot.send_message(message.chat.id, f"✅ Дата успешно перенесена на {format_date(new_date)}.", reply_markup=kb)
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат. Используйте ДД.ММ.ГГГГ")
        msg = bot.send_message(message.chat.id, "📅 Введите новую дату сдачи (ДД.ММ.ГГГГ):")
        bot.register_next_step_handler(msg, process_postpone_date, user_id)
    finally:
        if user_id in user_actions: del user_actions[user_id]

# Обновляем роутер меню, чтобы menu_my_tasks вызывал новый обработчик
# (Замените старую строку в menu_router на эту, если она не была обновлена)
# elif action == "my_tasks": show_my_tasks_main(call)

# ========== РАЗДЕЛ 8: БЫСТРЫЙ ПРОСМОТР ==========
def show_quick_actions(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📅 СЕГОДНЯ", callback_data="quick_today"),
           types.InlineKeyboardButton("📅 ЗАВТРА", callback_data="quick_tomorrow"),
           types.InlineKeyboardButton("📅 НЕДЕЛЯ", callback_data="quick_week"),
           types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="quick_overdue"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "⚡ БЫСТРЫЙ ПРОСМОТР", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("quick_"))
def quick_action_handler(call):
    bot.answer_callback_query(call.id)
    action = call.data.split("_")[1]
    kb_back = nav_buttons("menu_quick")

    if action == "today":
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending' ORDER BY t.due_date''', 
                          (call.from_user.id, today_str))
        tasks = cursor.fetchall()
        if not tasks: return safe_edit(call, f"📅 СЕГОДНЯ ({format_date(today_str)})\nНет запланированных работ. Отдыхай! 🎉", kb_back)
        text = f"📅 СЕГОДНЯ ({format_date(today_str)})\n\n" + "\n".join([f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} — {format_date(t['due_date'])}" for t in tasks])
        safe_edit(call, text, kb_back)
        
    elif action == "tomorrow":
        tom_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending' ORDER BY t.due_date''', 
                          (call.from_user.id, tom_str))
        tasks = cursor.fetchall()
        if not tasks: return safe_edit(call, f"📅 ЗАВТРА ({format_date(tom_str)})\nНет запланированных работ.", kb_back)
        text = f"📅 ЗАВТРА ({format_date(tom_str)})\n\n" + "\n".join([f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} — {format_date(t['due_date'])}" for t in tasks])
        safe_edit(call, text, kb_back)
        
    elif action == "week":
        today = datetime.now().date()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id
                          WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = 'pending' ORDER BY t.due_date''', 
                          (call.from_user.id, monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")))
        tasks = cursor.fetchall()
        if not tasks:
            return safe_edit(call, f"📅 НА НЕДЕЛЕ ({format_date(monday.strftime('%Y-%m-%d'))} - {format_date(sunday.strftime('%Y-%m-%d'))})\nНет работ.", kb_back)
        text = f"📅 НА НЕДЕЛЕ ({format_date(monday.strftime('%Y-%m-%d'))} - {format_date(sunday.strftime('%Y-%m-%d'))})\n\n" + "\n".join([f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} — {format_date(t['due_date'])}" for t in tasks])
        safe_edit(call, text, kb_back)
        
    elif action == "overdue":
        show_overdue_tasks(call.message.chat.id, call.from_user.id, callback_back="menu_quick")

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    logging.info("🚀 Бот запущен и готов к работе!")
    bot.infinity_polling(skip_pending=True)
