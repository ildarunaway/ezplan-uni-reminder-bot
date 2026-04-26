import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = "8651534837:AAGwfi4fUw8o8xKIaNO2CLpGDUbh23Gllrw"

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///university_bot.db")

# Notification settings
NOTIFICATION_CHECK_INTERVAL = 60  # seconds
DEFAULT_REMINDERS = {
    "control": [{"days_before": 3, "type": "day"}, {"days_before": 1, "type": "day"}, {"hours_before": 2, "type": "hour"}],
    "lab": [{"days_before": 2, "type": "day"}, {"days_before": 1, "type": "day"}, {"hours_before": 1, "type": "hour"}],
    "homework": [{"days_before": 1, "type": "day"}, {"hours_before": 2, "type": "hour"}],
    "exam": [{"days_before": 7, "type": "day"}, {"days_before": 3, "type": "day"}, {"days_before": 1, "type": "day"}, {"hours_before": 2, "type": "hour"}],
    "coursework": [{"days_before": 14, "type": "day"}, {"days_before": 7, "type": "day"}, {"days_before": 3, "type": "day"}, {"days_before": 1, "type": "day"}],
    "other": [{"days_before": 1, "type": "day"}]
}

# Aggregation settings
AGGREGATION_HOUR = 20  # 8 PM
AGGREGATION_THRESHOLD = 2  # минимум работ для агрегации

# Emoji mapping for subjects (default)
SUBJECT_EMOJIS = {
    "Математика": "📐",
    "Физика": "⚛️",
    "Программирование": "💻",
    "Английский": "🇬🇧",
    "История": "📜"
}

TASK_TYPE_EMOJIS = {
    "control": "📝",
    "lab": "🧪",
    "homework": "📚",
    "exam": "🎓",
    "coursework": "📖",
    "other": "📌"
}