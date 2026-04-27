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
conn = sqlite3.connect('university_bot.db', check_safe_thread=False)
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
    user_id INTEGER PRIMARY KEY, aggregation_enabled INTEGER DEFAULT 1,
    aggregation_hour INTEGER DEFAULT 20, quiet_hours_start INTEGER DEFAULT 23,
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

# Безопасный способ редактирования сообщения (избегает бесконечной загрузки при ошибках)
def safe_edit_text(call, text, reply_markup=None):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=reply_markup, parse_mode="Markdown")
    except telebot.apihelper.ApiTelegramException:
        bot.send_message(call.message.chat.id, text, reply_markup=reply_markup, parse_mode="Markdown")

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
        types.InlineKeyboardButton("📋 МОИ РАБОТЫ", callback_data="menu_tasks"), # Заменено Помощь
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

# Универсальные функции отображения (используются и в командах, и в быстрых действиях)
def send_today(chat_id, user_id):
    today_str = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.due_date = ? AND t.status = ?', (user_id, today_str, 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(chat_id, "📅 **НА СЕГОДНЯ**\nНет запланированных работ. Отдыхайте! 🎉", parse_mode="Markdown")
        return
    text = f"📅 **НА СЕГОДНЯ ({datetime.now().strftime('%d.%m.%Y')})**\n\n"
    for t in tasks:
        time_str = f" ⏰ {t[7]}" if t[7] else ""
        text += f"{t[13]} **{t[12]}**:\n  {TASK_TYPE_EMOJIS.get(t[4], '📌')} {t[3]}{time_str}\n\n"
    bot.send_message(chat_id, text, parse_mode="Markdown")

def send_tomorrow(chat_id, user_id):
    tom_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.due_date = ? AND t.status = ?', (user_id, tom_str, 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(chat_id, f"📅 **НА ЗАВТРА ({tom_str})**\nНет запланированных работ.", parse_mode="Markdown")
        return
    text = f"📅 **НА ЗАВТРА ({datetime.now().strftime('%d.%m.%Y')})**\n\n"
    for t in tasks:
        time_str = f" ⏰ {t[7]}" if t[7] else ""
        text += f"{t[13]} **{t[12]}**:\n  {TASK_TYPE_EMOJIS.get(t[4], '📌')} {t[3]}{time_str}\n\n"
    bot.send_message(chat_id, text, parse_mode="Markdown")

def send_week(chat_id, user_id):
    today = datetime.now().date()
    week_end = today + timedelta(days=7)
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.due_date BETWEEN ? AND ? AND t.status = ? ORDER BY t.due_date',
                   (user_id, today.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d"), 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(chat_id, "📅 **НА ЭТУ НЕДЕЛЮ**\nНет запланированных работ.", parse_mode="Markdown")
        return
    text = "📅 **ЗАДАЧИ НА ЭТУ НЕДЕЛЮ**\n\n"
    for t in tasks:
        due_date = format_date(t[6])
        # Если время не указано, выводим только дату
        time_str = f" в {t[7]}" if t[7] else ""
        text += f"{t[13]} **{t[12]}**: {t[3]} — {due_date}{time_str}\n"
    bot.send_message(chat_id, text, parse_mode="Markdown")

@bot.message_handler(commands=['today', 'tomorrow', 'week'])
def cmd_date(message):
    cmd = message.text.split()[0].replace('/', '')
    uid = message.from_user.id
    cid = message.chat.id
    if cmd == 'today': send_today(cid, uid)
    elif cmd == 'tomorrow': send_tomorrow(cid, uid)
    elif cmd == 'week': send_week(cid, uid)

@bot.message_handler(commands=['overdue'])
def cmd_overdue(message): show_overdue_tasks(message.from_user.id, message.chat.id)

@bot.message_handler(commands=['add'])
def cmd_add(message): start_add_flow(message.from_user.id, message.chat.id)

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
    safe_edit_text(call, "Главное меню:", reply_markup=get_main_keyboard())
    bot.answer_callback_query(call.id)

# ========== ПРЕДМЕТЫ (Исправлены индексы и бесконечная загрузка) ==========
def show_subjects(call):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ? ORDER BY name", (call.from_user.id,))
    subjects = cursor.fetchall()
    if not subjects:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"),
               types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
        safe_edit_text(call, "📚 **У вас пока нет предметов**\n\nДобавьте первый предмет, чтобы начать!", kb)
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
    safe_edit_text(call, "📚 **ВАШИ ПРЕДМЕТЫ**", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("subject_"))
def show_subject_details(call):
    subject_id = int(call.data.split("_")[1])
    cursor.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
    subject = cursor.fetchone()
    if not subject:
        bot.answer_callback_query(call.id, "Предмет не найден")
        return

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
        for t in overdue:
            days = (today - datetime.strptime(t[6], "%Y-%m-%d").date()).days
            text += f"  • {TASK_TYPE_EMOJIS.get(t[4], '📌')} {t[3]} — {format_date(t[6])} (просрочено на {days} дн.)\n"
        text += "\n"
    if upcoming:
        text += "📋 **ПРЕДСТОЯЩИЕ:**\n"
        for t in upcoming:
            time_str = f" в {t[7]}" if t[7] else ""
            text += f"  • {TASK_TYPE_EMOJIS.get(t[4], '📌')} {t[3]} — {format_date(t[6])}{time_str}\n"
        text += "\n"
    if completed:
        text += "✅ **ВЫПОЛНЕННЫЕ:**\n"
        for t in completed[:5]:
            text += f"  • {t[3]} — {format_date(t[6])}\n"
        if len(completed) > 5: text += f"  ... и ещё {len(completed)-5}\n"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ РАБОТУ", callback_data=f"add_to_subject_{subject_id}"),
           types.InlineKeyboardButton("✏️ РЕДАКТИРОВАТЬ", callback_data=f"edit_subject_{subject_id}"),
           types.InlineKeyboardButton("🗑 УДАЛИТЬ ПРЕДМЕТ", callback_data=f"del_subject_{subject_id}"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
    safe_edit_text(call, text, kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "add_subject")
def add_subject_prompt(call):
    msg = bot.send_message(call.message.chat.id, "📝 Введите название предмета:\n\nНапример: Математика, Физика", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_subject, call.message.chat.id)

def save_subject(message, chat_id):
    name = message.text.strip()
    if len(name) > 50:
        bot.send_message(chat_id, "❌ Название слишком длинное (максимум 50 символов)")
        return
    try:
        cursor.execute("INSERT INTO subjects (user_id, name) VALUES (?, ?)", (message.from_user.id, name))
        conn.commit()
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
               types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
        bot.send_message(chat_id, f"✅ Предмет **{name}** добавлен!", reply_markup=kb, parse_mode="Markdown")
    except sqlite3.IntegrityError:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("🔄 ПОПРОБОВАТЬ СНОВА", callback_data="add_subject"),
               types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_subjects"))
        bot.send_message(chat_id, f"❌ Предмет **{name}** уже существует!", reply_markup=kb, parse_mode="Markdown")

# ========== БЫСТРОЕ ДОБАВЛЕНИЕ ==========
user_temp = {}

def start_add_flow(user_id, chat_id, call=None):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ?", (user_id,))
    subjects = cursor.fetchall()
    if not subjects:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ПРЕДМЕТ", callback_data="add_subject"))
        if call: safe_edit_text(call, "📚 **Сначала добавьте предмет!**", kb)
        else: bot.send_message(chat_id, "📚 **Сначала добавьте предмет!**", reply_markup=kb, parse_mode="Markdown")
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    for sub in subjects:
        kb.add(types.InlineKeyboardButton(f"{sub[2]} {sub[1]}", callback_data=f"fast_subject_{sub[0]}"))
    if call: safe_edit_text(call, "📌 **Выберите предмет:**", kb)
    else: bot.send_message(chat_id, "📌 **Выберите предмет:**", reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_subject_"))
def fast_select_subject(call):
    user_temp[call.from_user.id] = {'subject_id': int(call.data.split("_")[2])}
    kb = types.InlineKeyboardMarkup(row_width=2)
    for name, ttype in TASK_TYPE_EMOJIS.items():
        kb.add(types.InlineKeyboardButton(f"{ttype} {name.capitalize()}", callback_data=f"fast_type_{ttype}"))
    bot.edit_message_text("📋 **Выберите тип работы:**", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_type_"))
def fast_select_type(call):
    user_temp[call.from_user.id]['task_type'] = call.data.split("_")[2]
    kb = types.InlineKeyboardMarkup(row_width=5)
    for i in range(1, 6): kb.add(types.InlineKeyboardButton(str(i), callback_data=f"fast_qty_{i}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_add")) # Кнопка назад
    bot.edit_message_text("🔢 **Сколько работ добавить? (1-5)**", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("fast_qty_"))
def fast_quantity(call):
    user_temp[call.from_user.id]['quantity'] = int(call.data.split("_")[2])
    user_temp[call.from_user.id]['tasks'] = []
    user_temp[call.from_user.id]['current'] = 0
    ask_task_title(call.from_user.id, call.message.chat.id)

def ask_task_title(user_id, chat_id):
    data = user_temp.get(user_id, {})
    current = data.get('current', 0)
    quantity = data.get('quantity', 1)
    if current >= quantity:
        save_all_tasks(user_id, chat_id)
        return
    data['current'] = current + 1
    user_temp[user_id] = data
    msg = bot.send_message(chat_id, f"📝 **Работа №{current+1} из {quantity}**\n\nВведите название работы:", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_task_title, user_id, chat_id)

def save_task_title(message, user_id, chat_id):
    data = user_temp.get(user_id, {})
    if 'tasks' not in data: data['tasks'] = []
    data['tasks'].append({'title': message.text.strip()})
    user_temp[user_id] = data
    msg = bot.send_message(chat_id, "📅 **Введите дату сдачи**\nФормат: ДД.ММ.ГГГГ", parse_mode="Markdown")
    bot.register_next_step_handler(msg, save_task_date, user_id, chat_id)

def save_task_date(message, user_id, chat_id):
    try: due_date = parse_date(message.text.strip())
    except: return bot.send_message(chat_id, "❌ Неверный формат! Используйте ДД.ММ.ГГГГ")
    data = user_temp.get(user_id, {})
    data['tasks'][-1]['due_date'] = due_date
    user_temp[user_id] = data
    ask_task_title(user_id, chat_id)

def save_all_tasks(user_id, chat_id):
    data = user_temp.get(user_id, {})
    saved = []
    for t in data.get('tasks', []):
        cursor.execute('INSERT INTO tasks (user_id, subject_id, title, task_type, due_date, due_time) VALUES (?, ?, ?, ?, ?, ?)',
                       (user_id, data['subject_id'], t['title'], data['task_type'], t['due_date'], t.get('due_time')))
        saved.append(t['title'])
    conn.commit()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="menu_add"),
           types.InlineKeyboardButton("📚 К ПРЕДМЕТАМ", callback_data="menu_subjects"),
           types.InlineKeyboardButton("🏠 В ГЛАВНОЕ МЕНЮ", callback_data="back_to_menu"))
    bot.send_message(chat_id, f"✅ **Добавлено {len(saved)} работ(а)!**\n" + "\n".join(f"• {s}" for s in saved), reply_markup=kb, parse_mode="Markdown")
    if user_id in user_temp: del user_temp[user_id]

# ========== ПРОСРОЧЕННЫЕ ==========
def show_overdue_tasks(user_id, chat_id):
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id = ? AND t.due_date < ? AND t.status = ? ORDER BY t.due_date', (user_id, today, 'pending'))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(chat_id, "✅ **Отлично! У вас нет просроченных работ.**", reply_markup=get_main_keyboard(), parse_mode="Markdown")
        return
    text = "⚠️ **ПРОСРОЧЕННЫЕ РАБОТЫ**\n\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for t in tasks:
        days = (datetime.now().date() - datetime.strptime(t[6], "%Y-%m-%d").date()).days
        text += f"{t[13]} {t[12]}:\n  • {t[3]} — просрочено на {days} дн.\n\n"
        kb.add(types.InlineKeyboardButton(f"✏️ {t[3][:30]}", callback_data=f"overdue_task_{t[0]}"))
    kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")

# Обработчики просроченных (сокращены для читаемости, логика аналогична оригиналу, но исправлены индексы)
@bot.callback_query_handler(func=lambda call: call.data.startswith("overdue_task_"))
def handle_overdue_task(call):
    task_id = int(call.data.split("_")[2])
    cursor.execute("SELECT t.*, s.name FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.id = ?", (task_id,))
    t = cursor.fetchone()
    if not t: return bot.answer_callback_query(call.id, "Задача не найдена")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📅 +1 ДЕНЬ", callback_data=f"postpone_{task_id}_1"),
           types.InlineKeyboardButton("📅 +3 ДНЯ", callback_data=f"postpone_{task_id}_3"),
           types.InlineKeyboardButton("📅 +1 НЕДЕЛЯ", callback_data=f"postpone_{task_id}_7"),
           types.InlineKeyboardButton("✅ СДАНО (С ОПОЗДАНИЕМ)", callback_data=f"complete_overdue_{task_id}"),
           types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"delete_overdue_{task_id}"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    bot.answer_callback_query(call.id)
    safe_edit_text(call, f"⚠️ **{t[3]}**\n📚 {t[11]}\n📅 Дедлайн: {format_date(t[6])}\n**Что сделать?**", kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith(("postpone_", "complete_overdue_", "delete_overdue_")))
def handle_task_action(call):
    parts = call.data.split("_")
    task_id = int(parts[1])
    if call.data.startswith("postpone_"):
        days = int(parts[2])
        cursor.execute("SELECT due_date FROM tasks WHERE id=?", (task_id,))
        new_date = (datetime.strptime(cursor.fetchone()[0], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("UPDATE tasks SET due_date=? WHERE id=?", (new_date, task_id))
        cursor.execute("DELETE FROM notifications_sent WHERE task_id=?", (task_id,))
        msg = f"✅ Перенесено на {format_date(new_date)}"
    elif call.data.startswith("complete_"):
        cursor.execute("UPDATE tasks SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
        msg = "✅ Отмечено как сданное!"
    else:
        cursor.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        msg = "🗑 Задача удалена"
    conn.commit()
    bot.answer_callback_query(call.id, msg)
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

# ========== КАЛЕНДАРЬ ==========
def show_calendar_menu(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📆 ПО МЕСЯЦАМ", callback_data="calendar_month"),
           types.InlineKeyboardButton("📋 ПО НЕДЕЛЯМ", callback_data="calendar_week"),
           types.InlineKeyboardButton("⚠️ ПРОСРОЧЕННЫЕ", callback_data="calendar_overdue"),
           types.InlineKeyboardButton("📊 НАГРУЗКА", callback_data="calendar_workload"),
           types.InlineKeyboardButton("🔗 ЭКСПОРТ", callback_data="calendar_export"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_text(call, "📅 **УМНЫЙ КАЛЕНДАРЬ**\nВыберите режим просмотра:", kb)

def calendar_month(call):
    today = datetime.now().date()
    end = today + timedelta(days=30)
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id=? AND t.due_date BETWEEN ? AND ? AND t.status=? ORDER BY t.due_date',
                   (call.from_user.id, today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), 'pending'))
    tasks = cursor.fetchall()
    text = f"📆 **НА БЛИЖАЙШИЙ МЕСЯЦ** ({format_date(today.strftime('%Y-%m-%d'))} - {format_date(end.strftime('%Y-%m-%d'))})\n\n"
    if not tasks: text += "Нет запланированных работ."
    else:
        current_date = None
        for t in tasks:
            if t[6] != current_date:
                current_date = t[6]
                text += f"\n**📅 {format_date(t[6])}:**\n"
            text += f"  {t[13]} {t[12]}: {t[3]}\n"
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar"))
    safe_edit_text(call, text, kb)
    bot.answer_callback_query(call.id)

def calendar_week(call):
    today = datetime.now().date()
    # Находим понедельник текущей недели
    start_week = today - timedelta(days=today.weekday())
    end_week = start_week + timedelta(days=6)
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id=? AND t.due_date BETWEEN ? AND ? AND t.status=? ORDER BY t.due_date',
                   (call.from_user.id, start_week.strftime("%Y-%m-%d"), end_week.strftime("%Y-%m-%d"), 'pending'))
    tasks = cursor.fetchall()
    text = f"📋 **ТЕКУЩАЯ НЕДЕЛЯ** ({format_date(start_week.strftime('%Y-%m-%d'))} - {format_date(end_week.strftime('%Y-%m-%d'))})\n\n"
    if not tasks: text += "Нет запланированных работ."
    else:
        current_date = None
        for t in tasks:
            if t[6] != current_date:
                current_date = t[6]
                day_name = datetime.strptime(t[6], "%Y-%m-%d").strftime("%A")
                text += f"\n**{day_name} ({format_date(t[6])}):**\n"
            text += f"  {t[13]} {t[12]}: {t[3]}\n"
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar"))
    safe_edit_text(call, text, kb)
    bot.answer_callback_query(call.id)

def calendar_workload(call):
    today = datetime.now().date()
    start_week = today - timedelta(days=today.weekday())
    end_week = start_week + timedelta(days=6)
    week_days = ['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС']
    text = f"📊 **НАГРУЗКА НА НЕДЕЛЮ** ({format_date(start_week.strftime('%Y-%m-%d'))} - {format_date(end_week.strftime('%Y-%m-%d'))})\n\n"
    
    for i in range(7):
        day_date = start_week + timedelta(days=i)
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id=? AND due_date=? AND status=?", (call.from_user.id, day_date.strftime("%Y-%m-%d"), 'pending'))
        count = cursor.fetchone()[0]
        bar = "█" * min(count, 8) + "░" * (8 - min(count, 8))
        text += f"{week_days[i]} ({format_date(day_date.strftime('%Y-%m-%d'))}): {bar} {count}\n"
    
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar"))
    safe_edit_text(call, text, kb)
    bot.answer_callback_query(call.id)

def calendar_export(call):
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_calendar"))
    safe_edit_text(call, "🔗 **ЭКСПОРТ В КАЛЕНДАРЬ**\nФункция будет доступна в следующей версии.\nПока используйте:\n/today, /week", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("calendar_"))
def cal_route(call):
    call.data == "calendar_overdue" and show_overdue_tasks(call.from_user.id, call.message.chat.id) or locals()[call.data.replace("_", "calendar_", 1)](call)
    bot.answer_callback_query(call.id)

# ========== НАПОМИНАНИЯ ==========
def show_reminders_menu(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⚙️ ШАБЛОНЫ", callback_data="reminder_templates"),
           types.InlineKeyboardButton("🧠 АГРЕГАЦИЯ", callback_data="reminder_aggregation"),
           types.InlineKeyboardButton("⏰ ВРЕМЯ ДАЙДЖЕСТА", callback_data="reminder_time"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_text(call, "🔔 **УМНЫЕ НАПОМИНАНИЯ**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reminder_"))
def rem_route(call):
    if call.data == "reminder_templates":
        safe_edit_text(call, "⚙️ **ШАБЛОНЫ**\n• 📝 Контрольная: 3д, 1д, 2ч\n• 🧪 Лабораторная: 2д, 1д, 1ч\n• 📚 ДЗ: 1д, 2ч\n• 🎓 Экзамен: 7д, 3д, 1д, 2ч", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders")))
    elif call.data == "reminder_aggregation":
        s = get_user_settings(call.from_user.id)
        safe_edit_text(call, f"🧠 **АГРЕГАЦИЯ**\nСтатус: {'✅ ВКЛ' if s['aggregation_enabled'] else '❌ ВЫКЛ'}\nВремя дайджеста: {s['aggregation_hour']}:00",
                       types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("🔘 ВКЛ/ВЫКЛ", callback_data="agg_toggle"), types.InlineKeyboardButton("⏰ ВРЕМЯ", callback_data="reminder_time"), types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders")))
    elif call.data == "reminder_time":
        kb = types.InlineKeyboardMarkup(row_width=4)
        for h in range(24): kb.add(types.InlineKeyboardButton(f"{h}:00", callback_data=f"set_hour_{h}"))
        kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_reminders"))
        safe_edit_text(call, "⏰ **ВРЕМЯ ДАЙДЖЕСТА (0-23)**", kb)
    elif call.data == "agg_toggle":
        s = get_user_settings(call.from_user.id)
        cursor.execute("UPDATE user_settings SET aggregation_enabled=? WHERE user_id=?", (0 if s['aggregation_enabled'] else 1, call.from_user.id))
        conn.commit()
        rem_route(call)
    elif call.data.startswith("set_hour_"):
        h = int(call.data.split("_")[2])
        cursor.execute("UPDATE user_settings SET aggregation_hour=? WHERE user_id=?", (h, call.from_user.id))
        conn.commit()
        bot.answer_callback_query(call.id, f"Установлено {h}:00")
        rem_route(call)
    bot.answer_callback_query(call.id)

# ========== МОИ РАБОТЫ (НОВЫЙ РАЗДЕЛ) ==========
TASK_TYPES_LIST = [("Все работы", "all"), ("📝 Контрольные", "control"), ("🧪 Лабораторные", "lab"), ("📚 Домашние", "homework"), ("📖 Курсовые", "coursework"), ("🎓 Экзамены", "exam")]

def show_my_tasks(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for name, ttype in TASK_TYPES_LIST:
        kb.add(types.InlineKeyboardButton(name, callback_data=f"mytasks_type_{ttype}"))
    kb.add(types.InlineKeyboardButton("🔍 ПО СТАТУСУ", callback_data="mytasks_status"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_text(call, "📋 **МОИ РАБОТЫ**\nВыберите категорию:", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mytasks_type_"))
def show_tasks_by_type(call):
    ttype = call.data.split("_")[2]
    cond = "AND t.status='pending'" if ttype == "all" else f"AND t.task_type='{ttype}' AND t.status='pending'"
    params = (call.from_user.id,)
    cursor.execute(f'SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id=? {cond} ORDER BY t.due_date', params)
    tasks = cursor.fetchall()
    text = f"📋 **{next((n for n,t in TASK_TYPES_LIST if t==ttype), 'Все работы')} (Активные)**\n\n"
    if not tasks: text += "Нет активных работ."
    else:
        for t in tasks:
            text += f"{t[13]} **{t[12]}**: {t[3]} — {format_date(t[6])}\n"
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_tasks"))
    safe_edit_text(call, text, kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mytask_sel_"))
def show_task_detail(call):
    tid = int(call.data.split("_")[2])
    cursor.execute("SELECT t.*, s.name FROM tasks t LEFT JOIN subjects s ON t.subject_id=s.id WHERE t.id=?", (tid,))
    t = cursor.fetchone()
    if not t: return bot.answer_callback_query(call.id, "Работа не найдена")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("✅ ВЫПОЛНЕНО", callback_data=f"mytask_done_{tid}"),
           types.InlineKeyboardButton("📅 ПЕРЕНЕСТИ", callback_data=f"mytask_postpone_{tid}"),
           types.InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f"mytask_del_{tid}"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_tasks"))
    safe_edit_text(call, f"📝 **{t[3]}**\n📚 {t[11]}\n📅 Дедлайн: {format_date(t[6])}", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith(("mytask_done_", "mytask_postpone_", "mytask_del_")))
def handle_my_task(call):
    parts = call.data.split("_")
    tid = int(parts[2])
    if parts[1] == "done":
        cursor.execute("UPDATE tasks SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?", (tid,))
        bot.answer_callback_query(call.id, "✅ Отмечено как выполненное!")
    elif parts[1] == "postpone":
        kb = types.InlineKeyboardMarkup(row_width=2)
        for d in [1, 3, 7]: kb.add(types.InlineKeyboardButton(f"+{d} дн.", callback_data=f"mytask_postpone_apply_{tid}_{d}"))
        kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data=f"mytask_sel_{tid}"))
        safe_edit_text(call, "📅 **На сколько перенести?**", kb)
        bot.answer_callback_query(call.id)
        return
    elif parts[1] == "del":
        cursor.execute("DELETE FROM tasks WHERE id=?", (tid,))
        bot.answer_callback_query(call.id, "🗑 Работа удалена")
    elif parts[1] == "postpone_apply":
        days = int(parts[4])
        cursor.execute("SELECT due_date FROM tasks WHERE id=?", (tid,))
        new_d = (datetime.strptime(cursor.fetchone()[0], "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute("UPDATE tasks SET due_date=? WHERE id=?", (new_d, tid))
        conn.commit()
        bot.answer_callback_query(call.id, f"✅ Перенесено на {format_date(new_d)}")
        show_tasks_by_type(call)
        return
    conn.commit()
    show_tasks_by_type(call)

@bot.callback_query_handler(func=lambda call: call.data == "mytasks_status")
def show_status_filter(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⏳ ОЖИДАЮЩИЕ", callback_data="mytasks_type_all"),
           types.InlineKeyboardButton("✅ ВЫПОЛНЕННЫЕ", callback_data="mytasks_type_completed"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_tasks"))
    safe_edit_text(call, "🔍 **ФИЛЬТР ПО СТАТУСУ**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "mytasks_type_completed")
def show_completed_tasks(call):
    cursor.execute('SELECT t.*, s.name, s.emoji FROM tasks t LEFT JOIN subjects s ON t.subject_id = s.id WHERE t.user_id=? AND t.status=? ORDER BY t.completed_at DESC', (call.from_user.id, 'completed'))
    tasks = cursor.fetchall()
    text = "✅ **ВЫПОЛНЕННЫЕ**\n"
    if not tasks: text += "Пока нет."
    for t in tasks[:15]: text += f"• {t[12]}: {t[3]} ({format_date(t[6])})\n"
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_tasks"))
    safe_edit_text(call, text, kb)
    bot.answer_callback_query(call.id)

# ========== СТАТИСТИКА & НАСТРОЙКИ ==========
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
    safe_edit_text(call, text, types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu")))
    bot.answer_callback_query(call.id)

def show_settings_menu(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="settings_profile"),
           types.InlineKeyboardButton("🔔 УВЕДОМЛЕНИЯ", callback_data="settings_notifications"),
           types.InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_menu"))
    safe_edit_text(call, "⚙️ **НАСТРОЙКИ**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("settings_"))
def settings_route(call):
    if call.data == "settings_profile":
        cursor.execute("SELECT * FROM users WHERE user_id=?", (call.from_user.id,))
        u = cursor.fetchone()
        kb = types.InlineKeyboardMarkup(row_width=1)
        for tz in ["UTC+3", "UTC+4", "UTC+5", "UTC+6", "UTC+7"]: kb.add(types.InlineKeyboardButton(f"🌍 {tz}", callback_data=f"tz_{tz}"))
        kb.add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings"))
        safe_edit_text(call, f"👤 **ПРОФИЛЬ**\nID: {u[0]}\nИмя: {u[2] or ''} {u[3] or ''}\nЧасовой пояс: {u[4]}", kb)
    elif call.data == "settings_notifications":
        s = get_user_settings(call.from_user.id)
        safe_edit_text(call, f"🔔 **УВЕДОМЛЕНИЯ**\n🕐 Тихие часы: {s['quiet_hours_start']}:00 - {s['quiet_hours_end']}:00\n🧠 Агрегация: {'✅' if s['aggregation_enabled'] else '❌'}", types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("◀️ НАЗАД", callback_data="menu_settings")))
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
    safe_edit_text(call, "⚡ **БЫСТРЫЕ ДЕЙСТВИЯ**", kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("quick_"))
def quick_action(call):
    action = call.data.split("_")[1]
    uid, cid = call.from_user.id, call.message.chat.id
    if action == "today": send_today(cid, uid)
    elif action == "tomorrow": send_tomorrow(cid, uid)
    elif action == "week": send_week(cid, uid)
    elif action == "overdue": show_overdue_tasks(uid, cid)
    elif action == "add": start_add_flow(uid, cid, call)
    bot.answer_callback_query(call.id)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("🚀 Бот запущен...")
    print(f"📊 Бот @{bot.get_me().username} готов!")
    bot.infinity_polling(skip_pending=True)
