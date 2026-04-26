import telebot
from telebot import types
import sqlite3
import datetime
import threading
import time
import json
import os

# ========== НАСТРОЙКА ==========
# Токен бота берётся из переменных окружения Render
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    print("Ошибка: переменная BOT_TOKEN не установлена!")
    exit(1)

bot = telebot.TeleBot(TOKEN)

# Подключение к базе данных SQLite (файл создастся автоматически)
conn = sqlite3.connect('university_bot.db', check_same_thread=False)
cursor = conn.cursor()

# Создание всех необходимых таблиц
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
    FOREIGN KEY(user_id) REFERENCES users(user_id),
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
    due_date TEXT,
    due_time TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id),
    FOREIGN KEY(subject_id) REFERENCES subjects(id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS task_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    user_id INTEGER,
    action TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
conn.commit()

# Эмодзи для типов задач
TASK_TYPE_EMOJIS = {
    "control": "📝",
    "lab": "🧪",
    "homework": "📚",
    "exam": "🎓",
    "coursework": "📖",
    "other": "📌"
}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_main_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("📚 Мои предметы", callback_data="menu_subjects")
    btn2 = types.InlineKeyboardButton("➕ Добавить работу", callback_data="menu_add_task")
    btn3 = types.InlineKeyboardButton("📅 Умный календарь", callback_data="menu_calendar")
    btn4 = types.InlineKeyboardButton("⚙️ Настройки", callback_data="menu_settings")
    keyboard.add(btn1, btn2, btn3, btn4)
    return keyboard

def get_or_create_user(user_id, username=None, first_name=None, last_name=None):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        conn.commit()
    return user

def format_task_list(tasks):
    if not tasks:
        return "📭 Нет задач."
    text = ""
    for task in tasks:
        due_date = datetime.datetime.strptime(task[5], "%Y-%m-%d").strftime("%d.%m.%Y")
        time_str = f" — {task[6]}" if task[6] else ""
        subject_name = task[10] if len(task) > 10 else "Без предмета"
        subject_emoji = task[11] if len(task) > 11 else "📚"
        text += f"{subject_emoji} **{subject_name}**:\n"
        text += f"  {TASK_TYPE_EMOJIS.get(task[4], '📌')} {task[3]} — {due_date}{time_str}\n\n"
    return text

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@bot.message_handler(commands=['start'])
def start(message):
    get_or_create_user(message.from_user.id, message.from_user.username, 
                       message.from_user.first_name, message.from_user.last_name)
    bot.send_message(message.chat.id, 
                     f"🎓 Привет, {message.from_user.first_name}!\n\nЯ помогу тебе не забыть о контрольных, лабораторных и других работах.\n\nНачни с добавления первого предмета!",
                     reply_markup=get_main_keyboard())

@bot.message_handler(commands=['today'])
def today(message):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        SELECT t.*, s.name, s.emoji FROM tasks t
        LEFT JOIN subjects s ON t.subject_id = s.id
        WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending'
    ''', (message.from_user.id, today))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(message.chat.id, "📅 На сегодня нет запланированных работ. Отдыхайте! 🎉")
        return
    text = f"📅 **НА СЕГОДНЯ ({datetime.datetime.now().strftime('%d.%m.%Y')})**\n\n{format_task_list(tasks)}"
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(commands=['overdue'])
def overdue(message):
    show_overdue_tasks(message.from_user.id, message.chat.id)

# ========== КОЛБЭКИ МЕНЮ ==========
@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def menu_callback(call):
    action = call.data.split("_")[1]
    if action == "subjects":
        show_subjects(call)
    elif action == "add_task":
        start_add_task(call)
    elif action == "calendar":
        show_calendar_menu(call)
    elif action == "settings":
        show_settings_menu(call)
    elif action == "stats":
        show_statistics(call)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
def back_to_menu(call):
    bot.edit_message_text("Главное меню:", call.message.chat.id, call.message.message_id,
                         reply_markup=get_main_keyboard())

# ========== ПРЕДМЕТЫ ==========
def show_subjects(call):
    cursor.execute("SELECT id, name, emoji FROM subjects WHERE user_id = ?", (call.from_user.id,))
    subjects = cursor.fetchall()
    if not subjects:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton("➕ Добавить предмет", callback_data="add_subject"))
        keyboard.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
        bot.edit_message_text("📚 У вас пока нет предметов. Добавьте первый!", 
                             call.message.chat.id, call.message.message_id, reply_markup=keyboard)
        return
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for subject in subjects:
        keyboard.add(types.InlineKeyboardButton(f"{subject[2]} {subject[1]}", callback_data=f"subject_{subject[0]}"))
    keyboard.add(types.InlineKeyboardButton("➕ Новый предмет", callback_data="add_subject"))
    keyboard.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
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
    ''', (call.from_user.id, subject_id))
    tasks = cursor.fetchall()
    upcoming = [t for t in tasks if t[7] == 'pending' and datetime.datetime.strptime(t[5], "%Y-%m-%d").date() >= datetime.datetime.now().date()]
    overdue_tasks = [t for t in tasks if t[7] == 'pending' and datetime.datetime.strptime(t[5], "%Y-%m-%d").date() < datetime.datetime.now().date()]
    text = f"{subject[3]} **{subject[2]}**\n\n"
    if overdue_tasks:
        text += "⚠️ **ПРОСРОЧЕННЫЕ:**\n"
        for task in overdue_tasks:
            due_date = datetime.datetime.strptime(task[5], "%Y-%m-%d").strftime("%d.%m.%Y")
            text += f"  • {task[3]} — {due_date}\n"
        text += "\n"
    if upcoming:
        text += "📋 **ПРЕДСТОЯЩИЕ:**\n"
        for task in upcoming:
            due_date = datetime.datetime.strptime(task[5], "%Y-%m-%d").strftime("%d.%m.%Y")
            text += f"  • {task[3]} — {due_date}\n"
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(types.InlineKeyboardButton("➕ Добавить работу", callback_data=f"add_to_subject_{subject_id}"))
    keyboard.add(types.InlineKeyboardButton("🗑 Удалить предмет", callback_data=f"del_subject_{subject_id}"))
    keyboard.add(types.InlineKeyboardButton("◀️ Назад", callback_data="menu_subjects"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "add_subject")
def add_subject_prompt(call):
    msg = bot.send_message(call.message.chat.id, "Введите название предмета:")
    bot.register_next_step_handler(msg, save_subject, call.message.chat.id)

def save_subject(message, chat_id):
    subject_name = message.text.strip()
    cursor.execute("INSERT INTO subjects (user_id, name) VALUES (?, ?)", (message.from_user.id, subject_name))
    conn.commit()
    bot.send_message(chat_id, f"✅ Предмет '{subject_name}' добавлен!", reply_markup=get_main_keyboard())

# ========== ДОБАВЛЕНИЕ ЗАДАЧИ ==========
def start_add_task(call):
    cursor.execute("SELECT id, name FROM subjects WHERE user_id = ?", (call.from_user.id,))
    subjects = cursor.fetchall()
    if not subjects:
        bot.answer_callback_query(call.id, "Сначала добавьте предмет!", show_alert=True)
        return
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for subject in subjects:
        keyboard.add(types.InlineKeyboardButton(subject[1], callback_data=f"sel_subject_{subject[0]}"))
    bot.edit_message_text("Выберите предмет:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

user_temp_data = {}
@bot.callback_query_handler(func=lambda call: call.data.startswith("sel_subject_"))
def select_subject_for_task(call):
    subject_id = int(call.data.split("_")[2])
    user_temp_data[call.from_user.id] = {'subject_id': subject_id}
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    task_types = [("📝 Контрольная", "control"), ("🧪 Лабораторная", "lab"), ("📚 ДЗ", "homework"), ("🎓 Экзамен", "exam")]
    for name, ttype in task_types:
        keyboard.add(types.InlineKeyboardButton(name, callback_data=f"ttype_{ttype}"))
    bot.edit_message_text("Выберите тип работы:", call.message.chat.id, call.message.message_id, reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("ttype_"))
def select_task_type(call):
    task_type = call.data.split("_")[1]
    user_temp_data[call.from_user.id]['task_type'] = task_type
    msg = bot.send_message(call.message.chat.id, "Введите название работы:")
    bot.register_next_step_handler(msg, get_task_title, call.from_user.id, call.message.chat.id)

def get_task_title(message, user_id, chat_id):
    user_temp_data[user_id]['title'] = message.text.strip()
    msg = bot.send_message(chat_id, "Введите дату сдачи (ДД.ММ.ГГГГ):")
    bot.register_next_step_handler(msg, get_task_date, user_id, chat_id)

def get_task_date(message, user_id, chat_id):
    try:
        due_date = datetime.datetime.strptime(message.text.strip(), "%d.%m.%Y").strftime("%Y-%m-%d")
        user_temp_data[user_id]['due_date'] = due_date
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(types.InlineKeyboardButton("Да", callback_data="time_yes"), types.InlineKeyboardButton("Нет", callback_data="time_no"))
        bot.send_message(chat_id, "Указать время сдачи?", reply_markup=keyboard)
    except:
        bot.send_message(chat_id, "Неверный формат! Попробуйте еще раз (ДД.ММ.ГГГГ)")

@bot.callback_query_handler(func=lambda call: call.data in ["time_yes", "time_no"])
def ask_time(call):
    if call.data == "time_yes":
        msg = bot.send_message(call.message.chat.id, "Введите время (ЧЧ:ММ):")
        bot.register_next_step_handler(msg, save_task_with_time, call.from_user.id, call.message.chat.id)
    else:
        save_task_final(call.from_user.id, call.message.chat.id, None)
    bot.delete_message(call.message.chat.id, call.message.message_id)

def save_task_with_time(message, user_id, chat_id):
    time_str = message.text.strip()
    save_task_final(user_id, chat_id, time_str)

def save_task_final(user_id, chat_id, due_time):
    data = user_temp_data.get(user_id, {})
    cursor.execute('''
        INSERT INTO tasks (user_id, subject_id, title, task_type, due_date, due_time)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, data['subject_id'], data['title'], data['task_type'], data['due_date'], due_time))
    conn.commit()
    bot.send_message(chat_id, f"✅ Задача '{data['title']}' добавлена!", reply_markup=get_main_keyboard())
    del user_temp_data[user_id]

# ========== ПРОСРОЧЕННЫЕ (СЦЕНАРИЙ Б) ==========
def show_overdue_tasks(user_id, chat_id):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cursor.execute('''
        SELECT t.*, s.name, s.emoji FROM tasks t
        LEFT JOIN subjects s ON t.subject_id = s.id
        WHERE t.user_id = ? AND t.due_date < ? AND t.status = 'pending'
    ''', (user_id, today))
    tasks = cursor.fetchall()
    if not tasks:
        bot.send_message(chat_id, "✅ Отлично! У вас нет просроченных работ.", reply_markup=get_main_keyboard())
        return
    text = "⚠️ **ПРОСРОЧЕННЫЕ РАБОТЫ**\n\n"
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for task in tasks:
        due_date = datetime.datetime.strptime(task[5], "%Y-%m-%d").date()
        days_overdue = (datetime.datetime.now().date() - due_date).days
        text += f"{task[11]} {task[10]}:\n  • {task[3]} — просрочено на {days_overdue} дн.\n"
        keyboard.add(types.InlineKeyboardButton(f"✏️ {task[3]}", callback_data=f"overdue_{task[0]}"))
    keyboard.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("overdue_"))
def handle_overdue_task(call):
    task_id = int(call.data.split("_")[1])
    cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    if not task:
        bot.answer_callback_query(call.id, "Задача не найдена")
        return
    due_date = datetime.datetime.strptime(task[5], "%Y-%m-%d").strftime("%d.%m.%Y")
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("➕ +1 день", callback_data=f"postpone_{task_id}_1"),
        types.InlineKeyboardButton("➕ +3 дня", callback_data=f"postpone_{task_id}_3"),
        types.InlineKeyboardButton("✅ Сдано", callback_data=f"complete_{task_id}"),
        types.InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_{task_id}")
    )
    keyboard.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    bot.edit_message_text(f"⚠️ **{task[3]}**\n📅 Дедлайн: {due_date}\n\nЧто сделать?",
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("postpone_"))
def postpone_task(call):
    parts = call.data.split("_")
    task_id = int(parts[1])
    days = int(parts[2])
    cursor.execute("SELECT due_date FROM tasks WHERE id = ?", (task_id,))
    old_date = cursor.fetchone()[0]
    new_date = (datetime.datetime.strptime(old_date, "%Y-%m-%d") + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    cursor.execute("UPDATE tasks SET due_date = ? WHERE id = ?", (new_date, task_id))
    conn.commit()
    bot.answer_callback_query(call.id, f"✅ Перенесено на {datetime.datetime.strptime(new_date, '%Y-%m-%d').strftime('%d.%m.%Y')}")
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("complete_"))
def complete_task(call):
    task_id = int(call.data.split("_")[1])
    cursor.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
    conn.commit()
    bot.answer_callback_query(call.id, "✅ Отмечено как сданное!")
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_"))
def delete_task(call):
    task_id = int(call.data.split("_")[1])
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    bot.answer_callback_query(call.id, "🗑 Задача удалена")
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

# ========== КАЛЕНДАРЬ И СТАТИСТИКА ==========
def show_calendar_menu(call):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton("⚠️ Просроченные", callback_data="menu_overdue"),
        types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")
    )
    bot.edit_message_text("📅 **УМНЫЙ КАЛЕНДАРЬ**\nВыберите режим:", 
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "menu_overdue")
def menu_overdue(call):
    show_overdue_tasks(call.from_user.id, call.message.chat.id)

def show_statistics(call):
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'completed'", (call.from_user.id,))
    completed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'pending'", (call.from_user.id,))
    pending = cursor.fetchone()[0]
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND due_date < ? AND status = 'pending'", (call.from_user.id, today))
    overdue_count = cursor.fetchone()[0]
    text = f"📊 **СТАТИСТИКА**\n\n✅ Выполнено: {completed}\n📋 Активных: {pending}\n⚠️ Просрочено: {overdue_count}"
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

def show_settings_menu(call):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu"))
    bot.edit_message_text("⚙️ **НАСТРОЙКИ**\nРаздел в разработке.", 
                         call.message.chat.id, call.message.message_id,
                         reply_markup=keyboard, parse_mode="Markdown")

# ========== ЗАПУСК ==========
print("Бот запущен и работает на Render...")
bot.infinity_polling()
