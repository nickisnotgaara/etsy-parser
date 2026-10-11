"""Прогоняет реальный лог парсинга через Telegram-рендерер.

Берёт файл лога, вытаскивает строки «LOG: ...» (их пишет LoggingEtsyMonitor)
и показывает, что именно увидел бы пользователь в Telegram.
"""
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.notifications import ParsingLogger  # noqa: E402


class NullSender:
    """NotificationService-заглушка: печатает текст вместо отправки."""

    def __init__(self):
        self.last = None

    async def send_message_to_user(self, user_id, message, parse_mode="HTML"):
        self.last = message
        return type("M", (), {"message_id": 1})()

    async def edit_message(self, chat_id, message_id, new_text, parse_mode="HTML"):
        self.last = new_text
        return True


def read_entries(log_path):
    """Достаёт записи лога последнего прогона."""
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    # Файл лога может содержать несколько прогонов подряд — берём последний,
    # иначе записи прошлого прогона склеятся с текущими.
    markers = [i for i, line in enumerate(lines) if " - LOG: 📋 Найдено" in line]
    if markers:
        lines = lines[markers[-1]:]

    entries = []
    for line in lines:
        match = re.search(r" - LOG: (.+)$", line)
        if match:
            entries.append(match.group(1).strip())
    return entries


async def main():
    log_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "logs/live_parse_no_telegram.log"
    )
    entries = read_entries(log_path)
    print(f"Найдено записей в {log_path.name}: {len(entries)}")

    sender = NullSender()
    logger = ParsingLogger(sender, user_id=998941250709)
    await logger.start_logging()

    new_products = 0
    for entry in entries:
        # LoggingEtsyMonitor так же сообщает логгеру общее число магазинов —
        # повторяем, иначе прогресс будет «магазины не определены».
        found = re.search(r"Найдено (\d+) магазинов", entry)
        if found:
            logger.set_total_shops(int(found.group(1)))

        found_new = re.search(r"Найдено (\d+) новых товаров", entry)
        if found_new:
            new_products = int(found_new.group(1))

        await logger.add_log_entry(entry)

    await logger.finish_logging(new_products)

    print("=" * 60)
    print("ЧТО ПРИШЛО БЫ В TELEGRAM:")
    print("=" * 60)
    print(sender.last)
    print("=" * 60)
    print(f"Длина сообщения: {len(sender.last)} символов (лимит Telegram 4096)")


if __name__ == "__main__":
    asyncio.run(main())