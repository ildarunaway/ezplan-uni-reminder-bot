import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_path: str = "./bot.db"
    default_timezone: str = "Europe/Moscow"
    admin_id: int | None = None


def get_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Add it to .env locally or Railway Variables.")

    admin_raw = os.getenv("ADMIN_ID", "").strip()
    return Settings(
        bot_token=token,
        database_path=os.getenv("DATABASE_PATH", "./bot.db").strip() or "./bot.db",
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow",
        admin_id=int(admin_raw) if admin_raw.isdigit() else None,
    )
