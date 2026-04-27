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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('university_bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
    timezone TEXT DEFAULT 'UTC', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT,
    emoji TEXT DEFAULT '📚', color TEXT DEFAULT '#3498db',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, name)
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, subject_id INTEGER, title TEXT,
    task_type TEXT, description TEXT, due_date DATE, due_time TIME,
    remind_before TEXT DEFAULT '[]', status TEXT DEFAULT 'pending',
    completed_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id), FOREIGN KEY (subject_id) REFERENCES subjects(id)
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS notifications_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, remind_type TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (task_id) REFERENCES tasks(id),
    UNIQUE(task_id, remind_type)
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY, notification_enabled INTEGER DEFAULT 1,
    notification_hour INTEGER DEFAULT 20, quiet_hours_start INTEGER DEFAULT 23,
    quiet_hours_end INTEGER DEFAULT 8, FOREIGN KEY (user_id) REFERENCES users(user_id)
)''')
conn.commit()

# ========== ЭМОДЗИ ==========
TASK_TYPE_EMOJIS = {
    "control": "📝", "lab": "🧪", "homework": "📚",
    "exam": "🎓", "coursework": "📖", "other": "📌"
}
SUBJECT_EMOJIS = ["📐", "⚛️", "💻", "🇬🇧", "📜", "🎨", "🔬", "💊", "⚖️", "📊", "🎵", "🏛️"]

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def safe_edit_message(call, text, reply_markup=None):
    """Безопасное редактирование сообщения с фоллбэком на отправку нового"""
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logging.debug(f"Edit failed, sending new message: {e}")
        bot.send_message(call.message.chat.id, text, reply_markup=reply_markup, parse_mode="Markdown")

def get_user_settings(user_id):
    cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
    settings = cursor.fetchone()
    if not settings:
        cursor.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return {"user_id": user_id, "notification_enabled": 1, "notification_hour": 20, "quiet_hours_start": 23, "quiet_hours_end": 8}
    return {"user_id": settings[0], "notification_enabled": settings[1], "notification_hour": settings[2], "quiet_hours_start": settings[3], "quiet_hours_end": settings[4]}

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
        types.InlineKeyboardButton("📋 МОИ РАБОТЫ", callback_data="menu_tasks"),
        types.InlineKeyboardButton("⚡ БЫСТРЫЕ ДЕЙСТВИЯ", callback_data="menu_quick")
    )
    return keyboard

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@bot.message_handler(commands=['start'])
def start(message):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    bot.send_message(message.chat.id,
        f"🎓 **Привет, {message.from_user.first_name.upper()}!**\n\n"
        f"Я — твой умный помощник по учёбе. Нажимай на кнопку ниже, чтобы начать! 👇",
        reply_markup=get_main_keyboard(), parse_mode="Markdown")

@bot.message_handler(commands=['today', 'tomorrow', 'week', 'overdue'])
def cmd_date(message):
    cmd = message.text.split()[0].replace('/', '')
    uid, cid = message.from_user.id, message.chat.id
    if cmd == 'today': show_today(cid, uid)
    elif cmd == 'tomorrow': show_tomorrow(cid, uid)
    elif cmd == 'week': show_week(cid, uid)
    elif cmd == 'overdue': show_overdue_tasks(uid, cid)

@bot.message_handler(commands=['add'])
def cmd_add(message): start_add_flow(message.from_user.id, message.chat.id)

# Универсальные функции для команд и быстрых действий
def show_today(cid, uid):
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.due_date = ? AND t.status = ? ORDER BY t.due_time', (uid, today, 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(cid, f"📅 **НА СЕГОДНЯ ({today})**\nНет запланированных работ. Отдыхайте! 🎉", parse_mode="Markdown")
        return
    text = f"📅 **НА СЕГОДНЯ ({format_date(today)})**\n\n"
    for t in tasks: text += f"{TASK_TYPE_EMOJIS.get(t[4], '📌')} **{t[3]}**\n📚 {t[12]} — 📅 {format_date(t[6])}\n\n"
    bot.send_message(cid, text, parse_mode="Markdown")

def show_tomorrow(cid, uid):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.due_date = ? AND t.status = ?', (uid, tomorrow, 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(cid, f"📅 **НА ЗАВТРА ({format_date(tomorrow)})**\nНет запланированных работ.", parse_mode="Markdown")
        return
    text = f"📅 **НА ЗАВТРА ({format_date(tomorrow)})**\n\n"
    for t in tasks: text += f"{TASK_TYPE_EMOJIS.get(t[4], '📌')} **{t[3]}**\n📚 {t[12]} — 📅 {format_date(t[6])}\n\n"
    bot.send_message(cid, text, parse_mode="Markdown")

def show_week(cid, uid):
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = ? ORDER BY t.due_date',
                   (uid, monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"), 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(cid, "📅 **НА ЭТУ НЕДЕЛЮ**\nНет запланированных работ.", parse_mode="Markdown")
        return
    text = f"📅 **НЕДЕЛЯ ({format_date(monday.strftime('%Y-%m-%d'))} - {format_date(sunday.strftime('%Y-%m-%d'))})**\n\n"
    for t in tasks: text += f"{TASK_TYPE_EMOJIS.get(t[4], '📌')} **{t[3]}**\n📚 {t[12]} — 📅 {format_date(t[6])}\n\n"
    bot.send_message(cid, text, parse_mode="Markdown")

# ========== МЕНЮ И НАВИГАЦИЯ ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def menu_callback(call):
    action = call.data.split("_")[1]
    if action == "subjects": show_subjects(call)
    elif action == "add": start_add_flow(call.from_user.id, call.message.chat.id, call)
    elif action == "calendar": show_calendar_menu(call)
    elif action == "reminders": show_reminders_menu(call)
    elif action == "settings": show_settings_menu(call)
    elif action == "stats": show_statistics(call)
    elif action == "tasks": show_my_tasks(call)
    elif action == "quick": show_quick_actions(call)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def back_to_menu(call):
    safe_edit_message(call, "🏠 **Главное меню**", reply_markup=get_main_keyboard())
    bot.answer_callback_query(call.id)

# ========== МОИ ПРЕДМЕТЫ (Исправлено) ==========
def show_subjects(call):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ? ORDER BY name", (call.from_user.id,))
    subjects = cursor.fetchall()
    if not subjects:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"),
               types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
        safe_edit_message(call, "📚 **У вас пока нет предметов**\n\nДобавьте первый предмет, чтобы начать!", kb)
        return

    kb = types.InlineKeyboardMarkup(row_width=1)
    for sub in subjects:
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE subject_id = ? AND status = ? AND due_date >= date('now')", (sub[0], 'pending'))
        upcoming = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE subject_id = ? AND status = ? AND due_date < date('now')", (sub[0], 'pending'))
        overdue = cursor.fetchone()[0]
        status = f"({upcoming} акт."
        if overdue > 0: status += f", {overdue} проср."
        status += ")"
        kb.add(types.InlineKeyboardButton(f"{sub[2]} {sub[1]} {status}", callback_data=f"subject_{sub[0]}"))
    kb.add(types.InlineKeyboardButton("➕ НОВЫЙ ПРЕДМЕТ", callback_data="add_subject"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_message(call, "📚 **ВАШИ ПРЕДМЕТЫ**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("subject_"))
def show_subject_details(call):
    subject_id = int(call.data.split("_")[1])
    cursor.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
    subject = cursor.fetchone()
    if not subject: return bot.answer_callback_query(call.id, "Предмет не найден")

    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.subject_id = ? ORDER BY t.due_date DESC', (call.from_user.id, subject_id))
    tasks = cursor.fetchall()

    upcoming, overdue, completed = [], [], []
    today = datetime.now().date()
    for t in tasks:
        due = datetime.strptime(t[6], "%Y-%m-%d").date()
        if t[9] == 'completed': completed.append(t)
        elif due < today: overdue.append(t)
        else: upcoming.append(t)

    text = f"{subject[3]} **{subject[2]}**\n\n"
    if overdue:
        text += "⚠️ **ПРОСРОЧЕННЫЕ:**\n"
        for t in overdue: text += f"  • {t[3]} — {format_date(t[6])} (просрочено на {(today - datetime.strptime(t[6], '%Y-%m-%d').date()).days} дн.)\n"
        text += "\n"
    if upcoming:
        text += "📋 **ПРЕДСТОЯЩИЕ:**\n"
        for t in upcoming: text += f"  • {t[3]} — {format_date(t[6])}\n"
        text += "\n"
    if completed:
        text += "✅ **ВЫПОЛНЕННЫЕ:**\n"
        for t in completed[:5]: text += f"  • {t[3]} — {format_date(t[6])}\n"
        if len(completed) > 5: text += f"  ... и ещё {len(completed)-5}\n"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ РАБОТУ", callback_data=f"add_to_subject_{subject_id}"),
           types.InlineKeyboardButton("✏️ РЕДАКТИРОВАТЬ РАБОТЫ", callback_data=f"edit_subject_tasks_{subject_id}"),
           types.InlineKeyboardButton("🗑 УДАЛИТЬ ПРЕДМЕТ", callback_data=f"del_subject_{subject_id}"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
    safe_edit_message(call, text, kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("add_to_subject_"))
def add_to_subject_prompt(call):
    subject_id = int(call.data.split("_")[2])
    user_temp = getattr(bot, '_user_temp', {})
    user_temp[call.from_user.id] = {'subject_id': subject_id, 'tasks': [], 'current': 0, 'quantity': 1, 'task_type': 'other'}
    bot._user_temp = user_temp
    fast_select_type(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_subject_tasks_"))
def edit_subject_tasks(call):
    subject_id = int(call.data.split("_")[3])
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.subject_id = ? AND t.status = ? ORDER BY t.due_date', (call.from_user.id, subject_id, 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        safe_edit_message(call, "📝 Нет активных работ для редактирования.", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"subject_{subject_id}")))
        return

    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks: kb.add(types.InlineKeyboardButton(f"📝 {t[3]} — {format_date(t[6])}", callback_data=f"task_action_{t[0]}"))
    if len(tasks) > 15: kb.add(types.InlineKeyboardButton("... (показано 15)", callback_data="none"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"subject_{subject_id}"))
    safe_edit_message(call, "📝 **ВЫБЕРИТЕ РАБОТУ ДЛЯ ДЕЙСТВИЯ:**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_subject_"))
def delete_subject(call):
    subject_id = int(call.data.split("_")[2])
    cursor.execute("SELECT name FROM subjects WHERE id=?", (subject_id,))
    name = cursor.fetchone()[0]
    cursor.execute("DELETE FROM tasks WHERE subject_id=?", (subject_id,))
    cursor.execute("DELETE FROM subjects WHERE id=?", (subject_id,))
    conn.commit()
    bot.answer_callback_query(call.id, f"🗑 Предмет '{name}' и все его работы удалены!")
    show_subjects(call)

@bot.callback_query_handler(func=lambda call: call.data == "add_subject")
def add_subject_prompt(call):
    msg = bot.send_message(call.message.chat.id, "📝 Введите название предмета:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_subject, call.message.chat.id)

def save_subject(message, chat_id):
    name = message.text.strip()
    if len(name) > 50: return bot.send_message(chat_id, "❌ Название слишком длинное (максимум 50 символов)")
    try:
        cursor.execute("INSERT INTO subjects (user_id, name) VALUES (?, ?)", (message.from_user.id, name))
        conn.commit()
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
               types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        bot.send_message(chat_id, f"✅ Предмет **{name}** добавлен!", reply_markup=kb, parse_mode="Markdown")
    except sqlite3.IntegrityError:
        bot.send_message(chat_id, f"❌ Предмет **{name}** уже существует!", parse_mode="Markdown")

# ========== БЫСТРОЕ ДОБАВЛЕНИЕ (Оставлено без изменений, адаптировано под новые вызовы) ==========
def start_add_flow(user_id, chat_id, call=None):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ?", (user_id,))
    subjects = cursor.fetchall()
    if not subjects:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"))
        if call: safe_edit_message(call, "📚 **Сначала добавьте предмет!**", kb)
        else: bot.send_message(chat_id, "📚 **Сначала добавьте предмет!**", reply_markup=kb, parse_mode="Markdown")
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    for sub in subjects: kb.add(types.InlineKeyboardButton(f"{sub[2]} {sub[1]}", callback_data=f"fast_subject_{sub[0]}"))
    if call: safe_edit_message(call, "📌 **Выберите предмет:**", kb)
    else: bot.send_message(chat_id, "📌 **Выберите предмет:**", reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_subject_"))
def fast_select_subject(call):
    user_temp = getattr(bot, '_user_temp', {})
    user_temp[call.from_user.id] = {'subject_id': int(call.data.split("_")[2])}
    bot._user_temp = user_temp
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    for name, ttype in TASK_TYPE_EMOJIS.items(): kb.add(types.InlineKeyboardButton(f"{ttype} {name.capitalize()}", callback_data=f"fast_type_{ttype}"))
    safe_edit_message(call, "📋 **Выберите тип работы:**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_type_"))
def fast_select_type(call):
    user_temp = getattr(bot, '_user_temp', {})
    data = user_temp.get(call.from_user.id, {})
    data['task_type'] = call.data.split("_")[2]
    user_temp[call.from_user.id] = data
    bot._user_temp = user_temp
    
    kb = types.InlineKeyboardMarkup(row_width=5)
    for i in range(1, 6): kb.add(types.InlineKeyboardButton(str(i), callback_data=f"fast_qty_{i}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_add"))
    safe_edit_message(call, "🔢 **Сколько работ добавить? (1-5)**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_qty_"))
def fast_quantity(call):
    user_temp = getattr(bot, '_user_temp', {})
    data = user_temp.get(call.from_user.id, {})
    data['quantity'] = int(call.data.split("_")[2])
    data['tasks'] = []
    data['current'] = 0
    user_temp[call.from_user.id] = data
    bot._user_temp = user_temp
    
    ask_task_title(call.from_user.id, call.message.chat.id, call)
    bot.answer_callback_query(call.id)

def ask_task_title(user_id, chat_id, call=None):
    user_temp = getattr(bot, '_user_temp', {})
    data = user_temp.get(user_id, {})
    current = data.get('current', 0)
    quantity = data.get('quantity', 1)
    if current >= quantity:
        save_all_tasks(user_id, chat_id)
        return
    data['current'] = current + 1
    user_temp[user_id] = data
    bot._user_temp = user_temp
    
    bot.send_message(chat_id, f"📝 **Работа №{current+1} из {quantity}**\n\nВведите название работы:", parse_mode="Markdown")
    bot.register_next_step_handler(chat_id, lambda m: save_task_title(m, user_id, chat_id))

def save_task_title(message, user_id, chat_id):
    user_temp = getattr(bot, '_user_temp', {})
    data = user_temp.get(user_id, {})
    if 'tasks' not in data: data['tasks'] = []
    data['tasks'].append({'title': message.text.strip(), 'due_date': ''})
    user_temp[user_id] = data
    bot._user_temp = user_temp
    
    bot.send_message(chat_id, "📅 **Введите дату сдачи**\nФормат: ДД.ММ.ГГГГ", parse_mode="Markdown")
    bot.register_next_step_handler(chat_id, lambda m: save_task_date(m, user_id, chat_id))

def save_task_date(message, user_id, chat_id):
    try: due_date = parse_date(message.text.strip())
    except: return bot.send_message(chat_id, "❌ Неверный формат! Используйте ДД.ММ.ГГГГ")
    user_temp = getattr(bot, '_user_temp', {})
    user_temp[user_id]['tasks'][-1]['due_date'] = due_date
    ask_task_title(user_id, chat_id)

def save_all_tasks(user_id, chat_id):
    user_temp = getattr(bot, '_user_temp', {})
    data = user_temp.get(user_id, {})
    saved = []
    for t in data.get('tasks', []):
        cursor.execute('INSERT INTO tasks (user_id, subject_id, title, task_type, due_date, due_time) VALUES (?, ?, ?, ?, ?, ?)',
                       (user_id, data['subject_id'], t['title'], data['task_type'], t['due_date'], None))
        saved.append(t['title'])
    conn.commit()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="menu_add"),
           types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
           types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    bot.send_message(chat_id, f"✅ **Добавлено {len(saved)} работ(а)!**\n" + "\n".join(f"• {s}" for s in saved), reply_markup=kb, parse_mode="Markdown")
    if user_id in user_temp: del user_temp[user_id]

# ========== УМНЫЙ КАЛЕНДАРЬ (Исправлено) ==========
def show_calendar_menu(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📆 НА МЕСЯЦ", callback_data="calendar_month"),
           types.InlineKeyboardButton("📋 НА НЕДЕЛЕ", callback_data="calendar_week"),
           types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="calendar_overdue"),
           types.InlineKeyboardButton("🔗 ЭКСПОРТ", callback_data="calendar_export"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_message(call, "📅 **УМНЫЙ КАЛЕНДАРЬ**\nВыберите режим просмотра:", kb)
    bot.answer_callback_query(call.id)

def calendar_month(call):
    today = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id=? AND t.due_date BETWEEN ? AND ? AND t.status=? ORDER BY t.due_date',
                   (call.from_user.id, today, end, 'pending'))
    tasks = cursor.fetchall()
    text = f"📆 **НА МЕСЯЦ ({format_date(today)} - {format_date(end)})**\n\n"
    if not tasks: text += "Нет запланированных работ."
    else:
        cur = None
        for t in tasks:
            if t[6] != cur:
                cur = t[6]
                text += f"\n**📅 {format_date(t[6])}:**\n"
            text += f"  {t[13]} {t[12]}: {t[3]}\n"
    safe_edit_message(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")))
    bot.answer_callback_query(call.id)

def calendar_week(call):
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id=? AND t.due_date BETWEEN ? AND ? AND t.status=? ORDER BY t.due_date',
                   (call.from_user.id, monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"), 'pending'))
    tasks = cursor.fetchall()
    text = f"📋 **НА НЕДЕЛЕ ({format_date(monday.strftime('%Y-%m-%d'))} - {format_date(sunday.strftime('%Y-%m-%d'))})**\n\n"
    if not tasks: text += "Нет запланированных работ."
    else:
        cur = None
        for t in tasks:
            if t[6] != cur:
                cur = t[6]
                text += f"\n**{datetime.strptime(t[6], '%Y-%m-%d').strftime('%A')} ({format_date(t[6])}):**\n"
            text += f"  {t[13]} {t[12]}: {t[3]}\n"
    safe_edit_message(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")))
    bot.answer_callback_query(call.id)

def calendar_overdue(call):
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id=? AND t.due_date < ? AND t.status=? ORDER BY t.due_date',
                   (call.from_user.id, today, 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        safe_edit_message(call, "✅ **Отлично! У вас нет просроченных работ.**", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")))
        return
    
    text = "⚠️ **ПРОСРОЧЕННЫЕ РАБОТЫ**\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        days = (datetime.now().date() - datetime.strptime(t[6], "%Y-%m-%d").date()).days
        text += f"\n{t[13]} {t[12]}:\n  • {t[3]} — просрочено на {days} дн.\n"
        kb.add(types.InlineKeyboardButton(f"📅 {t[3][:25]}", callback_data=f"overdue_task_{t[0]}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar"))
    safe_edit_message(call, text, kb)
    bot.answer_callback_query(call.id)

def calendar_export(call):
    safe_edit_message(call, "🔗 **ЭКСПОРТ В КАЛЕНДАРЬ**\nФункция будет доступна в следующей версии.\nПока используйте: /today, /week", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar")))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("calendar_"))
def cal_route(call):
    if call.data == "calendar_month": calendar_month(call)
    elif call.data == "calendar_week": calendar_week(call)
    elif call.data == "calendar_overdue": calendar_overdue(call)
    elif call.data == "calendar_export": calendar_export(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith(("postpone_", "complete_overdue_", "delete_overdue_", "overdue_task_")))
def handle_overdue_task(call):
    parts = call.data.split("_")
    if call.data.startswith("overdue_task_"):
        tid = int(parts[2])
        cursor.execute("SELECT t.*, s.name FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.id = ?", (tid,))
        t = cursor.fetchone()
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("📅 +1 ДЕНЬ", callback_data=f"postpone_{tid}_1"),
               types.InlineKeyboardButton("📅 +3 ДНЯ", callback_data=f"postpone_{tid}_3"),
               types.InlineKeyboardButton("📅 +1 НЕДЕЛЯ", callback_data=f"postpone_{tid}_7"),
               types.InlineKeyboardButton("✅ СДАНО", callback_data=f"complete_overdue_{tid}"),
               types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"delete_overdue_{tid}"),
               types.InlineKeyboardButton("◀️ НАЗАД", callback_data="calendar_overdue"))
        safe_edit_message(call, f"⚠️ **{t[3]}**\n📚 {t[11]}\n📅 Дедлайн: {format_date(t[6])}\n**Что сделать?**", kb)
    else:
        tid = int(parts[1])
        if parts[0] == "postpone":
            days = int(parts[2])
            cursor.execute("SELECT due_date FROM tasks WHERE id=?", (tid,))
            new_date = (datetime.strptime(cursor.fetchone()[0], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
            cursor.execute("UPDATE tasks SET due_date=? WHERE id=?", (new_date, tid))
            msg = f"✅ Перенесено на {format_date(new_date)}"
        elif parts[0] == "complete":
            cursor.execute("UPDATE tasks SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?", (tid,))
            msg = "✅ Отмечено как сданное!"
        else:
            cursor.execute("DELETE FROM tasks WHERE id=?", (tid,))
            msg = "🗑 Задача удалена"
        conn.commit()
        bot.answer_callback_query(call.id, msg)
        calendar_overdue(call)
    bot.answer_callback_query(call.id)

# ========== УМНЫЕ НАПОМИНАНИЯ (Исправлено) ==========
def show_reminders_menu(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⚙️ ШАБЛОНЫ", callback_data="reminder_templates"),
           types.InlineKeyboardButton("🧠 СИСТЕМА УВЕДОМЛЕНИЙ", callback_data="reminder_system"),
           types.InlineKeyboardButton("⏰ ВРЕМЯ ДАЙДЖЕСТА", callback_data="reminder_time"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_message(call, "🔔 **УМНЫЕ НАПОМИНАНИЯ**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reminder_"))
def rem_route(call):
    if call.data == "reminder_templates":
        safe_edit_message(call, "⚙️ **ШАБЛОНЫ НАПОМИНАНИЙ**\n• 📝 Контрольная: 3д, 1д до сдачи\n• 🧪 Лабораторная: 2д, 1д до сдачи\n• 📚 ДЗ: 1д до сдачи\n• 🎓 Экзамен: 7д, 3д, 1д до сдачи\n• 📖 Курсовая: 14д, 7д, 3д до сдачи", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders")))
    elif call.data == "reminder_system":
        s = get_user_settings(call.from_user.id)
        status = "✅ ВКЛ" if s['notification_enabled'] else "❌ ВЫКЛ"
        safe_edit_message(call, f"🧠 **СИСТЕМА УВЕДОМЛЕНИЙ**\nСтатус: {status}\nВремя дайджеста: {s['notification_hour']}:00",
                       types.InlineKeyboardMarkup(row_width=2).add(
                           types.InlineKeyboardButton("🔘 ВКЛ/ВЫКЛ", callback_data="notif_toggle"),
                           types.InlineKeyboardButton("⏰ ИЗМЕНИТЬ ВРЕМЯ", callback_data="reminder_time"),
                           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders")))
    elif call.data == "reminder_time":
        kb = types.InlineKeyboardMarkup(row_width=4)
        for h in range(24): kb.add(types.InlineKeyboardButton(f"{h}:00", callback_data=f"set_hour_{h}"))
        kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders"))
        safe_edit_message(call, "⏰ **ВЫБЕРИТЕ ВРЕМЯ (0-23)**", kb)
    elif call.data == "notif_toggle":
        s = get_user_settings(call.from_user.id)
        cursor.execute("UPDATE user_settings SET notification_enabled=? WHERE user_id=?", (0 if s['notification_enabled'] else 1, call.from_user.id))
        conn.commit()
        bot.answer_callback_query(call.id, "Настройка обновлена")
        rem_route(call)
    elif call.data.startswith("set_hour_"):
        h = int(call.data.split("_")[2])
        cursor.execute("UPDATE user_settings SET notification_hour=? WHERE user_id=?", (h, call.from_user.id))
        conn.commit()
        bot.answer_callback_query(call.id, f"Установлено {h}:00")
        rem_route(call)
    bot.answer_callback_query(call.id)

# ========== МОИ РАБОТЫ (Замена Помощи) ==========
TASK_TYPES_LIST = [("Все работы", "all"), ("📝 Контрольные", "control"), ("🧪 Лабораторные", "lab"), ("📚 Домашние", "homework"), ("📖 Курсовые", "coursework"), ("🎓 Экзамены", "exam")]

def show_my_tasks(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔍 ПО ТИПАМ РАБОТ", callback_data="mytasks_filter_type"),
           types.InlineKeyboardButton("🔍 ПО СТАТУСУ", callback_data="mytasks_filter_status"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_message(call, "📋 **МОИ РАБОТЫ**\nВыберите фильтр:", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mytasks_filter_"))
def mytasks_filter(call):
    mode = call.data.split("_")[2]
    kb = types.InlineKeyboardMarkup(row_width=1)
    if mode == "type":
        for name, ttype in TASK_TYPES_LIST: kb.add(types.InlineKeyboardButton(name, callback_data=f"mytasks_list_{ttype}_pending"))
    else:
        kb.add(types.InlineKeyboardButton("⏳ ОЖИДАЮТ", callback_data="mytasks_list_all_pending"),
               types.InlineKeyboardButton("✅ ВЫПОЛНЕНЫ", callback_data="mytasks_list_all_completed"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_tasks"))
    safe_edit_message(call, "🔍 **ВЫБЕРИТЕ ФИЛЬТР**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mytasks_list_"))
def show_my_tasks_list(call):
    parts = call.data.split("_")
    ttype = parts[2]
    status = parts[3]
    cond = f"AND t.task_type='{ttype}'" if ttype != "all" else ""
    cursor.execute(f'SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id=? AND t.status=? {cond} ORDER BY t.due_date', (call.from_user.id, status))
    tasks = cursor.fetchall()
    text = f"📋 **{ttype.capitalize()} ({'Активные' if status=='pending' else 'Выполненные'})**\n"
    if not tasks: text += "\nНет работ."
    else:
        for t in tasks: text += f"\n📚 {t[12]}: {t[3]} — 📅 {format_date(t[6])}"
        if len(tasks)>10: text+="\n... (показано 10)"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks[:10]: kb.add(types.InlineKeyboardButton(f"📝 {t[12]}: {t[3][:20]}", callback_data=f"mytask_detail_{t[0]}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_tasks"))
    safe_edit_message(call, text, kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mytask_"))
def mytask_detail(call):
    tid = int(call.data.split("_")[2])
    cursor.execute("SELECT t.*, s.name FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id WHERE t.id=?", (tid,))
    t = cursor.fetchone()
    if not t: return bot.answer_callback_query(call.id, "Работа не найдена")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("✅ ВЫПОЛНЕНО", callback_data=f"mytask_done_{tid}"),
           types.InlineKeyboardButton("📅 ПЕРЕНЕСТИ", callback_data=f"mytask_postpone_{tid}"),
           types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"mytask_del_{tid}"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_tasks"))
    safe_edit_message(call, f"📝 **{t[3]}**\n📚 {t[11]}\n📅 Дедлайн: {format_date(t[6])}", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith(("mytask_done_", "mytask_postpone_", "mytask_del_")))
def handle_my_task(call):
    parts = call.data.split("_")
    tid = int(parts[2])
    if parts[1] == "done":
        cursor.execute("UPDATE tasks SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?", (tid,))
        conn.commit()
        bot.answer_callback_query(call.id, "✅ Отмечено как выполненное!")
    elif parts[1] == "del":
        cursor.execute("DELETE FROM tasks WHERE id=?", (tid,))
        conn.commit()
        bot.answer_callback_query(call.id, "🗑 Работа удалена")
    elif parts[1] == "postpone":
        kb = types.InlineKeyboardMarkup(row_width=2)
        for d in [1, 3, 7]: kb.add(types.InlineKeyboardButton(f"+{d} дн.", callback_data=f"mytask_postpone_apply_{tid}_{d}"))
        kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"mytask_detail_{tid}"))
        safe_edit_message(call, "📅 **На сколько перенести?**", kb)
        bot.answer_callback_query(call.id)
        return
    elif parts[1] == "postpone_apply":
        days = int(parts[3])
        cursor.execute("SELECT due_date FROM tasks WHERE id=?", (tid,))
        new_d = (datetime.strptime(cursor.fetchone()[0], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("UPDATE tasks SET due_date=? WHERE id=?", (new_d, tid))
        conn.commit()
        bot.answer_callback_query(call.id, f"✅ Перенесено на {format_date(new_d)}")
    bot.answer_callback_query(call.id)
    show_my_tasks(call)

# ========== СТАТИСТИКА & НАСТРОЙКИ (Без изменений) ==========
def show_statistics(call):
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status=?", (call.from_user.id, 'completed'))
    comp = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND status=?", (call.from_user.id, 'pending'))
    pend = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND due_date<? AND status=?", (call.from_user.id, datetime.now().strftime("%Y-%m-%d"), 'pending'))
    ov = cursor.fetchone()[0]
    tot = comp + pend + ov
    rate = (comp / tot * 100) if tot else 0
    text = f"📊 **СТАТИСТИКА**\n✅ Выполнено: {comp}\n⚠️ Просрочено: {ov}\n📋 Активных: {pend}\n📈 Процент: {rate:.1f}%"
    safe_edit_message(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")))
    bot.answer_callback_query(call.id)

def show_settings_menu(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="settings_profile"),
           types.InlineKeyboardButton("🔔 УВЕДОМЛЕНИЯ", callback_data="settings_notifications"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_message(call, "⚙️ **НАСТРОЙКИ**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("settings_"))
def settings_route(call):
    if call.data == "settings_profile":
        cursor.execute("SELECT * FROM users WHERE user_id=?", (call.from_user.id,))
        u = cursor.fetchone()
        kb = types.InlineKeyboardMarkup(row_width=1)
        for tz in ["UTC+3", "UTC+4", "UTC+5", "UTC+6", "UTC+7"]: kb.add(types.InlineKeyboardButton(f"🌍 {tz}", callback_data=f"tz_{tz}"))
        kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
        safe_edit_message(call, f"👤 **ПРОФИЛЬ**\nID: {u[0]}\nИмя: {u[2] or ''} {u[3] or ''}\nЧасовой пояс: {u[4]}", kb)
    elif call.data == "settings_notifications":
        s = get_user_settings(call.from_user.id)
        safe_edit_message(call, f"🔔 **УВЕДОМЛЕНИЯ**\n🕐 Тихие часы: {s['quiet_hours_start']}:00 - {s['quiet_hours_end']}:00\n🧠 Система: {'✅' if s['notification_enabled'] else '❌'}", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings")))
    elif call.data.startswith("tz_"):
        cursor.execute("UPDATE users SET timezone=? WHERE user_id=?", (call.data.split("_")[1], call.from_user.id))
        conn.commit()
        bot.answer_callback_query(call.id, "✅ Часовой пояс обновлён")
        settings_route(call)
    bot.answer_callback_query(call.id)

# ========== БЫСТРЫЕ ДЕЙСТВИЯ (Исправлено) ==========
def show_quick_actions(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📅 СЕГОДНЯ", callback_data="quick_today"),
           types.InlineKeyboardButton("📅 ЗАВТРА", callback_data="quick_tomorrow"),
           types.InlineKeyboardButton("📅 НЕДЕЛЯ", callback_data="quick_week"),
           types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="quick_overdue"),
           types.InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data="quick_add"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_message(call, "⚡ **БЫСТРЫЕ ДЕЙСТВИЯ**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("quick_"))
def quick_action(call):
    action = call.data.split("_")[1]
    uid, cid = call.from_user.id, call.message.chat.id
    if action == "today": show_today(cid, uid)
    elif action == "tomorrow": show_tomorrow(cid, uid)
    elif action == "week": show_week(cid, uid)
    elif action == "overdue": show_overdue_tasks(uid, cid)
    elif action == "add": start_add_flow(uid, cid, call)
    bot.answer_callback_query(call.id)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("🚀 Бот запущен...")
    bot.infinity_polling(skip_pending=True)
