import telebot
from telebot import types
import sqlite3
from datetime import datetime, timedelta
import os
import logging
import threading
import time

# ========== НАСТРОЙКА ==========
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    logging.error("Ошибка: переменная BOT_TOKEN не установлена!")
    exit(1)

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
DB_LOCK = threading.Lock()
conn = sqlite3.connect('university_bot.db', check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

with DB_LOCK:
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
    CREATE TABLE IF NOT EXISTS reminder_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_id INTEGER,
        notify_date DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    ''')
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

user_actions = {}
user_temp = {}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_user_settings(user_id):
    with DB_LOCK:
        cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
        s = cursor.fetchone()
        if not s:
            cursor.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return {"aggregation_enabled": 1, "aggregation_hour": 20, "quiet_hours_start": 23, "quiet_hours_end": 8}
        return {
            "aggregation_enabled": s['aggregation_enabled'],
            "aggregation_hour": s['aggregation_hour'],
            "quiet_hours_start": s.get('quiet_hours_start', 23),
            "quiet_hours_end": s.get('quiet_hours_end', 8)
        }

def get_or_create_user(user_id, username, first_name, last_name):
    with DB_LOCK:
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
                           (user_id, username, first_name, last_name))
            conn.commit()

def format_date(date_str):
    try: return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except: return date_str

def parse_date(date_str):
    return datetime.strptime(date_str.strip(), "%d.%m.%Y").strftime("%Y-%m-%d")

def safe_edit(call, text, reply_markup=None, parse_mode="Markdown"):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=reply_markup, parse_mode=parse_mode)

def nav_buttons(section_callback="back_to_menu"):
    kb = types.InlineKeyboardMarkup(row_width=2)
    if section_callback:
        kb.add(types.InlineKeyboardButton("◀️ В РАЗДЕЛ", callback_data=section_callback))
    kb.add(types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    return kb

# ========== ГЛАВНОЕ МЕНЮ ==========
def get_main_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📚 МОИ ПРЕДМЕТЫ", callback_data="menu_subjects"),
           types.InlineKeyboardButton("➕ БЫСТРОЕ ДОБАВЛЕНИЕ", callback_data="menu_add"))
    kb.add(types.InlineKeyboardButton("📅 УМНЫЙ КАЛЕНДАРЬ", callback_data="menu_calendar"),
           types.InlineKeyboardButton("🔔 УМНЫЕ НАПОМИНАНИЯ", callback_data="menu_reminders"))
    kb.add(types.InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data="menu_settings"),
           types.InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="menu_stats"))
    kb.add(types.InlineKeyboardButton("📂 МОИ РАБОТЫ", callback_data="menu_my_tasks"),
           types.InlineKeyboardButton("⚡ БЫСТРЫЙ ПРОСМОТР", callback_data="menu_quick"))
    return kb

# ========== ОБРАБОТЧИКИ ==========
@bot.message_handler(commands=['start'])
def start(message):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎓 НАЧАТЬ", callback_data="menu_subjects"))
    bot.send_message(message.chat.id, f"🎓 Привет, {message.from_user.first_name.upper()}!\nЯ — твой умный помощник по учёбе.", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("menu_"))
def menu_router(call):
    bot.answer_callback_query(call.id)
    action = call.data.split("_")[1]
    if action == "subjects": show_subjects(call)
    elif action == "add": start_add_flow(call.from_user.id, call.message.chat.id, call)
    elif action == "calendar": show_calendar_menu(call)
    elif action == "reminders": show_reminders_menu(call)
    elif action == "settings": show_settings_menu(call)
    elif action == "stats": show_statistics(call)
    elif action == "quick": show_quick_actions(call)
    elif action == "my_tasks": show_my_tasks_main(call)

@bot.callback_query_handler(func=lambda c: c.data == "back_to_menu")
def back_to_menu(call):
    bot.answer_callback_query(call.id)
    bot.edit_message_text("Главное меню:", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())

# ========== 1. МОИ ПРЕДМЕТЫ ==========
def show_subjects(call):
    with DB_LOCK:
        cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ? ORDER BY name", (call.from_user.id,))
        subs = cursor.fetchall()
    if not subs:
        return safe_edit(call, "📚 У вас пока нет предметов.", types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data="add_subject"),
            types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")))
    kb = types.InlineKeyboardMarkup(row_width=1)
    for s in subs: kb.add(types.InlineKeyboardButton(f"{s['emoji']} {s['name']}", callback_data=f"subject_{s['id']}"))
    kb.add(types.InlineKeyboardButton("➕ НОВЫЙ ПРЕДМЕТ", callback_data="add_subject"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "📚 ВАШИ ПРЕДМЕТЫ", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("subject_"))
def show_subject_details(call):
    bot.answer_callback_query(call.id)
    sid = int(call.data.split("_")[1])
    with DB_LOCK:
        cursor.execute("SELECT * FROM subjects WHERE id=?", (sid,))
        subj = cursor.fetchone()
        cursor.execute('''SELECT * FROM tasks WHERE user_id=? AND subject_id=? AND status='pending' ORDER BY due_date''', (call.from_user.id, sid))
        tasks = cursor.fetchall()
    today = datetime.now().date()
    overdue = [t for t in tasks if datetime.strptime(t['due_date'], "%Y-%m-%d").date() < today]
    upcoming = [t for t in tasks if datetime.strptime(t['due_date'], "%Y-%m-%d").date() >= today]
    text = f"{subj['emoji']} **{subj['name']}**\n"
    if overdue: text += "\n⚠️ ПРОСРОЧЕННЫЕ:\n" + "\n".join(f"  • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']} — {format_date(t['due_date'])}" for t in overdue)
    if upcoming: text += "\n📋 ПРЕДСТОЯЩИЕ:\n" + "\n".join(f"  • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']} — {format_date(t['due_date'])}" for t in upcoming)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data=f"add_to_subject_{sid}"),
           types.InlineKeyboardButton("✏️ РЕДАКТИРОВАТЬ", callback_data=f"edit_subject_{sid}"),
           types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"del_subject_{sid}"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
    safe_edit(call, text, kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("add_to_subject_"))
def add_to_subject(call):
    bot.answer_callback_query(call.id)
    sid = int(call.data.split("_")[3])
    user_temp[call.from_user.id] = {'subject_id': sid, 'preselected': True}
    start_add_flow(call.from_user.id, call.message.chat.id, call)

@bot.callback_query_handler(func=lambda c: c.data == "add_subject")
def add_subject_prompt(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📝 Введите название предмета:")
    bot.register_next_step_handler(msg, save_subject)

def save_subject(message):
    name = message.text.strip()
    if len(name) > 50: return bot.send_message(message.chat.id, "❌ Слишком длинное название.")
    try:
        with DB_LOCK:
            cursor.execute("INSERT INTO subjects (user_id, name) VALUES (?, ?)", (message.from_user.id, name))
            conn.commit()
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
                                              types.InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        bot.send_message(message.chat.id, f"✅ Предмет **{name}** добавлен!", reply_markup=kb, parse_mode="Markdown")
    except sqlite3.IntegrityError:
        bot.send_message(message.chat.id, f"❌ Предмет уже существует!", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔄 ЕЩЁ РАЗ", callback_data="add_subject")))

@bot.callback_query_handler(func=lambda c: c.data.startswith("del_subject_"))
def delete_subject(call):
    bot.answer_callback_query(call.id, "🗑 Удалено!")
    sid = int(call.data.split("_")[2])
    with DB_LOCK:
        cursor.execute("DELETE FROM tasks WHERE subject_id=?", (sid,))
        cursor.execute("DELETE FROM subjects WHERE id=?", (sid,))
        conn.commit()
    safe_edit(call, "🗑 Предмет и работы удалены.", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ К ПРЕДМЕТАМ", callback_data="menu_subjects")))

@bot.callback_query_handler(func=lambda c: c.data.startswith("edit_subject_"))
def edit_subject_tasks(call):
    bot.answer_callback_query(call.id)
    sid = int(call.data.split("_")[2])
    with DB_LOCK:
        cursor.execute("SELECT name FROM subjects WHERE id=?", (sid,))
        name = cursor.fetchone()['name']
        cursor.execute('''SELECT t.*, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                          WHERE t.user_id=? AND t.subject_id=? AND t.status='pending' ORDER BY t.due_date''', (call.from_user.id, sid))
        tasks = cursor.fetchall()
    if not tasks:
        return safe_edit(call, f"✏️ РАБОТЫ ПО ПРЕДМЕТУ: {name}\nНет активных работ.", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"subject_{sid}")))
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks: kb.add(types.InlineKeyboardButton(f"{TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']} ({format_date(t['due_date'])})", callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"subject_{sid}"))
    safe_edit(call, f"✏️ РЕДАКТИРОВАНИЕ: {name}\nВыберите работу:", kb)

# ========== 2. БЫСТРОЕ ДОБАВЛЕНИЕ ==========
def start_add_flow(user_id, chat_id, call=None):
    with DB_LOCK:
        cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ?", (user_id,))
        subs = cursor.fetchall()
    if not subs:
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"), types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
        return safe_edit(call, "📚 Сначала добавьте предмет!", kb) if call else bot.send_message(chat_id, "📚 Сначала добавьте предмет!", reply_markup=kb)
    kb = types.InlineKeyboardMarkup(row_width=2)
    for s in subs: kb.add(types.InlineKeyboardButton(f"{s['emoji']} {s['name']}", callback_data=f"fast_subject_{s['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "📌 Выберите предмет:", kb) if call else bot.send_message(chat_id, "📌 Выберите предмет:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("fast_subject_"))
def fast_select_subject(call):
    bot.answer_callback_query(call.id)
    user_temp[call.from_user.id] = {'subject_id': int(call.data.split("_")[2])}
    kb = types.InlineKeyboardMarkup(row_width=2)
    for k, v in TASK_TYPE_EMOJIS.items(): kb.add(types.InlineKeyboardButton(v, callback_data=f"fast_type_{k}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_add"))
    bot.edit_message_text("📋 Выберите тип работы:", call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("fast_type_"))
def fast_select_type(call):
    bot.answer_callback_query(call.id)
    user_temp[call.from_user.id]['task_type'] = call.data.split("_")[2]
    kb = types.InlineKeyboardMarkup(row_width=5)
    for i in range(1, 6): kb.add(types.InlineKeyboardButton(str(i), callback_data=f"fast_qty_{i}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_add"))
    bot.edit_message_text("🔢 Сколько работ добавить? (1-5)", call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("fast_qty_"))
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
    msg = bot.send_message(chat_id, f"📝 Работа №{data['current']} из {data['quantity']}\nВведите название:")
    bot.register_next_step_handler(msg, save_task_title, user_id, chat_id)

def save_task_title(message, user_id, chat_id):
    data = user_temp.get(user_id, {})
    data['tasks'].append({'title': message.text.strip()})
    user_temp[user_id] = data
    msg = bot.send_message(chat_id, "📅 Введите дату сдачи (ДД.ММ.ГГГГ):")
    bot.register_next_step_handler(msg, save_task_date, user_id, chat_id)

def save_task_date(message, user_id, chat_id):
    try:
        due = parse_date(message.text.strip())
    except:
        bot.send_message(chat_id, "❌ Неверный формат! ДД.ММ.ГГГГ")
        ask_task_title(user_id, chat_id)
        return
    data = user_temp.get(user_id, {})
    data['tasks'][data['current']-1]['due_date'] = due
    user_temp[user_id] = data
    ask_task_title(user_id, chat_id)

def save_all_tasks(user_id, chat_id):
    data = user_temp.get(user_id, {})
    saved = []
    with DB_LOCK:
        for t in data['tasks']:
            cursor.execute("INSERT INTO tasks (user_id, subject_id, title, task_type, due_date) VALUES (?, ?, ?, ?, ?)",
                           (user_id, data['subject_id'], t['title'], data['task_type'], t['due_date']))
            saved.append(t['title'])
        conn.commit()
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("➕ ЕЩЁ", callback_data="menu_add"),
                                          types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
                                          types.InlineKeyboardButton("🏠 МЕНЮ", callback_data="back_to_menu"))
    bot.send_message(chat_id, f"✅ Добавлено {len(saved)} работ:\n" + "\n".join(f"  • {t}" for t in saved), reply_markup=kb)
    if user_id in user_temp: del user_temp[user_id]

# ========== 3. КАЛЕНДАРЬ ==========
def show_calendar_menu(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📆 МЕСЯЦ", callback_data="calendar_month"), types.InlineKeyboardButton("📋 НЕДЕЛЯ", callback_data="calendar_week"))
    kb.add(types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="calendar_overdue"), types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "📅 КАЛЕНДАРЬ", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("calendar_"))
def calendar_handler(call):
    bot.answer_callback_query(call.id)
    action = call.data.split("_")[1]
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar"))
    if action == "month":
        today = datetime.now().date()
        end = today + timedelta(days=30)
        with DB_LOCK:
            cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                              WHERE t.user_id=? AND t.due_date BETWEEN ? AND ? AND t.status='pending' ORDER BY t.due_date''',
                           (call.from_user.id, today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
            tasks = cursor.fetchall()
        if not tasks: return safe_edit(call, "📆 Нет работ на месяц.", kb)
        text = "📆 РАБОТЫ НА МЕСЯЦ:\n"
        curr = None
        for t in tasks:
            if curr != t['due_date']: curr = t['due_date']; text += f"\n**{format_date(curr)}:**\n"
            text += f"  {t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']}\n"
        safe_edit(call, text, kb)
    elif action == "week":
        today = datetime.now().date()
        mon = today - timedelta(days=today.weekday())
        sun = mon + timedelta(days=6)
        with DB_LOCK:
            cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                              WHERE t.user_id=? AND t.due_date BETWEEN ? AND ? AND t.status='pending' ORDER BY t.due_date''',
                           (call.from_user.id, mon.strftime("%Y-%m-%d"), sun.strftime("%Y-%m-%d")))
            tasks = cursor.fetchall()
        if not tasks: return safe_edit(call, "📋 Нет работ на неделю.", kb)
        text = f"📋 НЕДЕЛЯ ({format_date(mon.strftime('%Y-%m-%d'))} - {format_date(sun.strftime('%Y-%m-%d'))})\n\n"
        for t in tasks: text += f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'], '📌')} {t['title']} — {format_date(t['due_date'])}\n"
        safe_edit(call, text, kb)
    elif action == "overdue":
        show_overdue_tasks(call.message.chat.id, call.from_user.id, "menu_calendar")

# ========== 4. НАПОМИНАНИЯ (ИСПРАВЛЕНО) ==========
def show_reminders_menu(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⚙️ ШАБЛОНЫ", callback_data="reminder_templates"),
           types.InlineKeyboardButton("🧠 СИСТЕМА УВЕДОМЛЕНИЙ", callback_data="reminder_system"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "🔔 НАПОМИНАНИЯ", kb)

@bot.callback_query_handler(func=lambda c: c.data == "reminder_templates")
def reminder_templates(call):
    bot.answer_callback_query(call.id)
    safe_edit(call, "⚙️ ШАБЛОНЫ:\n• 📝 Контрольная: за 3, 1 день\n• 🧪 Лаб: за 2, 1 день\n• 📚 ДЗ: за 1 день, 3 часа",
              types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders")))

@bot.callback_query_handler(func=lambda c: c.data == "reminder_system")
def reminder_system(call):
    bot.answer_callback_query(call.id)
    s = get_user_settings(call.from_user.id)
    status = "✅ ВКЛЮЧЕНА" if s['aggregation_enabled'] else "❌ ВЫКЛЮЧЕНА"
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔘 ВКЛ/ВЫКЛ", callback_data="agg_toggle"),
           types.InlineKeyboardButton("⏰ ВРЕМЯ ДАЙДЖЕСТА", callback_data="reminder_change_time"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders"))
    safe_edit(call, f"🧠 СИСТЕМА УВЕДОМЛЕНИЙ\nСтатус: {status}\nВремя дайджеста: {s['aggregation_hour']}:00", kb)

@bot.callback_query_handler(func=lambda c: c.data == "agg_toggle")
def toggle_aggregation(call):
    bot.answer_callback_query(call.id)
    s = get_user_settings(call.from_user.id)
    new_val = 0 if s['aggregation_enabled'] else 1
    with DB_LOCK:
        cursor.execute("UPDATE user_settings SET aggregation_enabled = ? WHERE user_id = ?", (new_val, call.from_user.id))
        conn.commit()
    bot.answer_callback_query(call.id, f"Система {'включена' if new_val else 'выключена'}!", show_alert=True)
    reminder_system(call)

@bot.callback_query_handler(func=lambda c: c.data == "reminder_change_time")
def change_reminder_time(call):
    bot.answer_callback_query(call.id)
    s = get_user_settings(call.from_user.id)
    kb = types.InlineKeyboardMarkup(row_width=4)
    for h in range(24):
        txt = f"✅ {h:02}:00" if h == s['aggregation_hour'] else f"{h:02}:00"
        kb.add(types.InlineKeyboardButton(txt, callback_data=f"set_hour_{h}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="reminder_system"))
    bot.edit_message_text("⏰ ВЫБЕРИТЕ ВРЕМЯ:", call.message.chat.id, call.message.message_id, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_hour_"))
def set_agg_hour(call):
    bot.answer_callback_query(call.id)
    h = int(call.data.split("_")[2])
    with DB_LOCK:
        cursor.execute("UPDATE user_settings SET aggregation_hour = ? WHERE user_id = ?", (h, call.from_user.id))
        conn.commit()
    bot.answer_callback_query(call.id, f"⏰ Установлено на {h:02}:00", show_alert=True)
    reminder_system(call)

# ========== 5. НАСТРОЙКИ (ИСПРАВЛЕНО) ==========
def show_settings_menu(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="settings_profile"),
           types.InlineKeyboardButton("🔔 УВЕДОМЛЕНИЯ", callback_data="settings_notifications"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "⚙️ НАСТРОЙКИ", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("settings_"))
def settings_router(call):
    bot.answer_callback_query(call.id)
    if call.data == "settings_profile":
        with DB_LOCK:
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (call.from_user.id,))
            u = cursor.fetchone()
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏛️ ИЗМЕНИТЬ ВУЗ", callback_data="settings_university"),
                                              types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
        safe_edit(call, f"👤 ПРОФИЛЬ:\nID: {u['user_id']}\nИмя: {u['first_name'] or '—'} {u['last_name'] or ''}\n🏛️ ВУЗ: {u['university'] or 'Не указан'}", kb)
    elif call.data == "settings_university":
        msg = bot.send_message(call.message.chat.id, "🏛️ Введите название ВУЗа:")
        bot.register_next_step_handler(msg, save_university, call.from_user.id)
    elif call.data == "settings_notifications":
        s = get_user_settings(call.from_user.id)
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
        safe_edit(call, f"🔔 НАСТРОЙКИ УВЕДОМЛЕНИЙ:\n🧠 Система: {'✅ Вкл' if s['aggregation_enabled'] else '❌ Выкл'}\n⏰ Дайджест: {s['aggregation_hour']}:00\n🌙 Тихие часы: {s['quiet_hours_start']}:00 - {s['quiet_hours_end']}:00", kb)

def save_university(message, user_id):
    uni = message.text.strip()
    if len(uni) > 100: return bot.send_message(message.chat.id, "❌ Слишком длинно.")
    with DB_LOCK:
        cursor.execute("UPDATE users SET university = ? WHERE user_id = ?", (uni, user_id))
        conn.commit()
    bot.send_message(message.chat.id, f"✅ ВУЗ изменён на: **{uni}**", reply_markup=nav_buttons("menu_settings"), parse_mode="Markdown")

# ========== 6. СТАТИСТИКА ==========
def show_statistics(call):
    bot.answer_callback_query(call.id)
    with DB_LOCK:
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status='completed'", (call.from_user.id,))
        comp = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status='pending'", (call.from_user.id,))
        pend = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND due_date<? AND status='pending'", (call.from_user.id, datetime.now().date().strftime("%Y-%m-%d")))
        overdue = cursor.fetchone()[0]
    total = comp + pend + overdue
    rate = (comp / total * 100) if total > 0 else 0
    text = f"📊 СТАТИСТИКА:\n✅ Выполнено: {comp}\n⚠️ Просрочено: {overdue}\n📋 Активных: {pend}\n📈 Успех: {rate:.1f}%"
    safe_edit(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")))

# ========== 7. МОИ РАБОТЫ (ПОЛНОСТЬЮ ИСПРАВЛЕНО) ==========
def show_my_tasks_main(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📋 ПО ТИПАМ РАБОТ", callback_data="tasks_by_type"))
    kb.add(types.InlineKeyboardButton("📊 ПО СТАТУСУ", callback_data="tasks_by_status"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "📂 МОИ РАБОТЫ\nВыберите фильтр:", kb)

@bot.callback_query_handler(func=lambda c: c.data == "tasks_by_type")
def show_type_filter(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    for k, v in TASK_TYPE_EMOJIS.items():
        kb.add(types.InlineKeyboardButton(v, callback_data=f"filter_type_{k}"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"))
    safe_edit(call, "📋 ВЫБЕРИТЕ ТИП:", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("filter_type_"))
def filter_by_type(call):
    bot.answer_callback_query(call.id)
    t_type = call.data.split("_")[2]
    with DB_LOCK:
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                          WHERE t.user_id=? AND t.task_type=? AND t.status='pending' ORDER BY t.due_date''',
                       (call.from_user.id, t_type))
        tasks = cursor.fetchall()
    show_task_list(call, tasks, f"📋 {TASK_TYPE_EMOJIS.get(t_type, '📌')}", "menu_my_tasks")

@bot.callback_query_handler(func=lambda c: c.data == "tasks_by_status")
def show_status_filter(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("✅ ВЫПОЛНЕННЫЕ", callback_data="filter_status_completed"))
    kb.add(types.InlineKeyboardButton("⏳ ЗАПЛАНИРОВАННЫЕ", callback_data="filter_status_pending"))
    kb.add(types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="filter_status_overdue"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"))
    safe_edit(call, "📊 ВЫБЕРИТЕ СТАТУС:", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("filter_status_"))
def filter_by_status(call):
    bot.answer_callback_query(call.id)
    s_type = call.data.split("_")[2]
    today = datetime.now().date().strftime("%Y-%m-%d")
    if s_type == "completed":
        query = '''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                   WHERE t.user_id=? AND t.status='completed' ORDER BY t.completed_at DESC'''
        title, params = "✅ ВЫПОЛНЕННЫЕ", (call.from_user.id,)
    elif s_type == "pending":
        query = '''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                   WHERE t.user_id=? AND t.due_date>=? AND t.status='pending' ORDER BY t.due_date'''
        title, params = "⏳ ЗАПЛАНИРОВАННЫЕ", (call.from_user.id, today)
    elif s_type == "overdue":
        query = '''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                   WHERE t.user_id=? AND t.due_date<? AND t.status='pending' ORDER BY t.due_date'''
        title, params = "⚠️ ПРОСРОЧЕННЫЕ", (call.from_user.id, today)
    else: return
    with DB_LOCK:
        cursor.execute(query, params)
        tasks = cursor.fetchall()
    show_task_list(call, tasks, title, "menu_my_tasks")

def show_task_list(call, tasks, title, back_cb):
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not tasks:
        kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"),
               types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        safe_edit(call, f"{title}\nРабот не найдено.", kb)
        return
    for t in tasks:
        kb.add(types.InlineKeyboardButton(f"{t['emoji']} {t['name']} • {t['title']} ({format_date(t['due_date'])})", callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data=back_cb),
           types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    safe_edit(call, f"{title} ({len(tasks)} шт):", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("my_task_"))
def task_menu(call):
    bot.answer_callback_query(call.id)
    tid = int(call.data.split("_")[2])
    with DB_LOCK:
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id WHERE t.id=?''', (tid,))
        t = cursor.fetchone()
    if not t: return bot.answer_callback_query(call.id, "Задача не найдена")
    kb = types.InlineKeyboardMarkup(row_width=1)
    if t['status'] == 'pending':
        kb.add(types.InlineKeyboardButton("✅ ВЫПОЛНЕНА", callback_data=f"task_complete_{tid}"),
               types.InlineKeyboardButton("📅 ПЕРЕНЕСТИ", callback_data=f"task_postpone_{tid}"),
               types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"task_delete_{tid}"))
    kb.add(types.InlineKeyboardButton("◀️ В МОИ РАБОТЫ", callback_data="menu_my_tasks"),
           types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    safe_edit(call, f"📝 **{t['title']}**\n📚 {t['name']}\n📅 Сдача: {format_date(t['due_date'])}\nСтатус: {t['status']}", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("task_complete_"))
def task_complete(call):
    bot.answer_callback_query(call.id, "✅ Выполнено!")
    tid = int(call.data.split("_")[2])
    with DB_LOCK:
        cursor.execute("UPDATE tasks SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?", (tid,))
        conn.commit()
    bot.send_message(call.message.chat.id, "✅ Работа отмечена выполненной.", reply_markup=nav_buttons("menu_my_tasks"))

@bot.callback_query_handler(func=lambda c: c.data.startswith("task_postpone_"))
def task_postpone(call):
    bot.answer_callback_query(call.id)
    tid = int(call.data.split("_")[2])
    user_actions[call.from_user.id] = {'action': 'postpone', 'task_id': tid}
    msg = bot.send_message(call.message.chat.id, "📅 Введите новую дату (ДД.ММ.ГГГГ):")
    bot.register_next_step_handler(msg, process_postpone, call.from_user.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("task_delete_"))
def task_delete(call):
    bot.answer_callback_query(call.id, "🗑 Удалено!")
    tid = int(call.data.split("_")[2])
    with DB_LOCK:
        cursor.execute("DELETE FROM tasks WHERE id=?", (tid,))
        conn.commit()
    bot.send_message(call.message.chat.id, "🗑 Работа удалена.", reply_markup=nav_buttons("menu_my_tasks"))

def process_postpone(message, user_id):
    act = user_actions.get(user_id)
    if not act: return
    try:
        new_date = parse_date(message.text)
        with DB_LOCK:
            cursor.execute("UPDATE tasks SET due_date=? WHERE id=?", (new_date, act['task_id']))
            conn.commit()
        bot.send_message(message.chat.id, f"✅ Перенесено на **{format_date(new_date)}**.", reply_markup=nav_buttons("menu_my_tasks"), parse_mode="Markdown")
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат. ДД.ММ.ГГГГ")
        msg = bot.send_message(message.chat.id, "📅 Введите новую дату:")
        bot.register_next_step_handler(msg, process_postpone, user_id)
    finally:
        if user_id in user_actions: del user_actions[user_id]

# ========== 8. БЫСТРЫЙ ПРОСМОТР ==========
def show_quick_actions(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📅 СЕГОДНЯ", callback_data="quick_today"), types.InlineKeyboardButton("📅 ЗАВТРА", callback_data="quick_tomorrow"))
    kb.add(types.InlineKeyboardButton("📅 НЕДЕЛЯ", callback_data="quick_week"), types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="quick_overdue"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit(call, "⚡ БЫСТРЫЙ ПРОСМОТР", kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("quick_"))
def quick_handler(call):
    bot.answer_callback_query(call.id)
    action = call.data.split("_")[1]
    kb = nav_buttons("menu_quick")
    today_str = datetime.now().strftime("%Y-%m-%d")
    if action == "today":
        with DB_LOCK:
            cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                              WHERE t.user_id=? AND t.due_date=? AND t.status='pending' ORDER BY t.due_date''', (call.from_user.id, today_str))
            tasks = cursor.fetchall()
        if not tasks: return safe_edit(call, f"📅 СЕГОДНЯ\nНет работ.", kb)
        text = "📅 СЕГОДНЯ:\n" + "\n".join(f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']}" for t in tasks)
        safe_edit(call, text, kb)
    elif action == "tomorrow":
        tom = (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d")
        with DB_LOCK:
            cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                              WHERE t.user_id=? AND t.due_date=? AND t.status='pending' ORDER BY t.due_date''', (call.from_user.id, tom))
            tasks = cursor.fetchall()
        if not tasks: return safe_edit(call, f"📅 ЗАВТРА\nНет работ.", kb)
        text = "📅 ЗАВТРА:\n" + "\n".join(f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']}" for t in tasks)
        safe_edit(call, text, kb)
    elif action == "week":
        today = datetime.now().date()
        mon = today - timedelta(days=today.weekday())
        sun = mon + timedelta(days=6)
        with DB_LOCK:
            cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                              WHERE t.user_id=? AND t.due_date BETWEEN ? AND ? AND t.status='pending' ORDER BY t.due_date''',
                           (call.from_user.id, mon.strftime("%Y-%m-%d"), sun.strftime("%Y-%m-%d")))
            tasks = cursor.fetchall()
        if not tasks: return safe_edit(call, "📅 НЕДЕЛЯ: Нет работ.", kb)
        text = "📅 НЕДЕЛЯ:\n" + "\n".join(f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']}" for t in tasks)
        safe_edit(call, text, kb)
    elif action == "overdue":
        show_overdue_tasks(call.message.chat.id, call.from_user.id, "menu_quick")

def show_overdue_tasks(chat_id, user_id, back_cb):
    today = datetime.now().date().strftime("%Y-%m-%d")
    with DB_LOCK:
        cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                          WHERE t.user_id=? AND t.due_date<? AND t.status='pending' ORDER BY t.due_date''', (user_id, today))
        tasks = cursor.fetchall()
    if not tasks: return bot.send_message(chat_id, "✅ Просрочек нет!", reply_markup=nav_buttons(back_cb))
    text = "⚠️ ПРОСРОЧЕННЫЕ:\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        days = (datetime.now().date() - datetime.strptime(t['due_date'], "%Y-%m-%d").date()).days
        text += f"\n{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']}\n  ⏰ +{days} дн."
        kb.add(types.InlineKeyboardButton(f"⚙️ {t['title'][:25]}", callback_data=f"my_task_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=back_cb))
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")

# ========== ФОНОВЫЙ ПЛАНИРОВЩИК (ИСПРАВЛЕН) ==========
def scheduler():
    while True:
        time.sleep(60)
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            with DB_LOCK:
                cursor.execute("SELECT user_id FROM users")
                users = cursor.fetchall()
                for u in users:
                    uid = u['user_id']
                    s = get_user_settings(uid)
                    if s['aggregation_enabled']:
                        if now.hour == s['aggregation_hour']:
                            cursor.execute("SELECT * FROM reminder_log WHERE user_id=? AND notify_date=? AND task_id IS NULL", (uid, today_str))
                            if not cursor.fetchone():
                                cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                                                  WHERE t.user_id=? AND t.due_date=? AND t.status='pending' ORDER BY t.due_date''', (uid, today_str))
                                tasks = cursor.fetchall()
                                if tasks:
                                    txt = f"📅 Вечерний дайджест ({format_date(today_str)}):\n"
                                    for t in tasks: txt += f"{t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']}\n"
                                    try:
                                        bot.send_message(uid, txt, parse_mode="Markdown")
                                        cursor.execute("INSERT INTO reminder_log (user_id, notify_date) VALUES (?, ?)", (uid, today_str))
                                        conn.commit()
                                    except: pass
                    else:
                        if now.hour == 9:
                            cursor.execute('''SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id
                                              WHERE t.user_id=? AND t.due_date=? AND t.status='pending' ORDER BY t.due_date''', (uid, today_str))
                            tasks = cursor.fetchall()
                            for t in tasks:
                                cursor.execute("SELECT * FROM reminder_log WHERE task_id=? AND user_id=? AND notify_date=?", (t['id'], uid, today_str))
                                if not cursor.fetchone():
                                    txt = f"⏰ Напоминание: {t['emoji']} {t['name']} • {TASK_TYPE_EMOJIS.get(t['task_type'],'📌')} {t['title']}"
                                    try:
                                        bot.send_message(uid, txt, parse_mode="Markdown")
                                        cursor.execute("INSERT INTO reminder_log (task_id, user_id, notify_date) VALUES (?, ?, ?)", (t['id'], uid, today_str))
                                        conn.commit()
                                    except: pass
        except Exception as e:
            logging.error(f"Scheduler error: {e}")

threading.Thread(target=scheduler, daemon=True).start()

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    logging.info("🚀 Бот запущен!")
    bot.infinity_polling(skip_pending=True)
