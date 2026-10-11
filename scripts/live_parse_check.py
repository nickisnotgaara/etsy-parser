"""Живая проверка парсинга: реальный Etsy + реальный Telegram.

Запускает ровно тот путь, что и кнопка «Запустить парсинг» в боте:
    BotScheduler.scheduled_parsing_job(user_id=ADMIN_ID)

Планировщики НЕ поднимаются — иначе в фоне поедут другие задачи.
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/live_check.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402

from bot.config import config  # noqa: E402
from bot.database import BotDatabase  # noqa: E402
from bot.notifications import NotificationService  # noqa: E402
from bot.scheduler_integration import BotScheduler  # noqa: E402


async def main():
    if not config.BOT_TOKEN:
        print("BOT_TOKEN не найден в config-main.txt")
        return 1
    if not config.ADMIN_ID:
        print("ADMIN_ID не найден в config-main.txt")
        return 1

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    db = BotDatabase(config.DATABASE_PATH)
    await db.init_database()

    try:
        me = await bot.get_me()
        print(f"Бот авторизован: @{me.username}")
        print(f"ADMIN_ID: {config.ADMIN_ID}")
        print(f"БД: {config.DATABASE_PATH}")
        print("-" * 60)

        admins = await db.get_all_admins()
        print(f"Админов в БД: {[a[0] for a in admins]}")

        notification_service = NotificationService(bot, db)
        scheduler = BotScheduler(notification_service, db)

        started = asyncio.get_running_loop().time()
        # user_id=None — как при автозапуске по расписанию (лог всем админам).
        await scheduler.scheduled_parsing_job(user_id=None)
        elapsed = asyncio.get_running_loop().time() - started

        print("-" * 60)
        print(f"Прогон завершён за {elapsed:.1f} сек")
        return 0
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()) or 0)
    except KeyboardInterrupt:
        print("Прервано пользователем")
        sys.exit(130)