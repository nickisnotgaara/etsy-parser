"""Живой прогон парсинга Etsy без Telegram.

Тот же код-путь, что у бота (LoggingEtsyMonitor), но логгер — консольный:
реальный Selenium/Chrome/прокси/Google Sheets, без отправки в Telegram.
"""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/live_parse_no_telegram.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)

from bot.scheduler_integration import LoggingEtsyMonitor, ParserLock  # noqa: E402
from core.monitor import EtsyMonitor  # noqa: E402

CONFIG_PATH = ROOT / "config-main.txt"


class ConsoleLogger:
    """Заглушка Telegram-логгера: печатает записи в консоль."""

    def __init__(self):
        self.entries = []

    async def start_logging(self):
        pass

    def set_total_shops(self, total):
        print(f"[ЛОГ] всего магазинов: {total}", flush=True)

    async def add_log_entry(self, entry):
        self.entries.append(entry)
        print(f"[ЛОГ] {entry}", flush=True)

    async def tick(self):
        pass

    async def mark_stopped(self):
        pass


async def main():
    # Бот ставит этот флаг перед парсингом; без него цикл сразу считает
    # себя остановленным пользователем. Исходный файл восстановим в finally.
    config_backup = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else None
    lock = ParserLock()
    lock.set_working()
    print(f"Статус парсера: {lock.get_status()}", flush=True)

    try:
        monitor = EtsyMonitor()
        logger = ConsoleLogger()
        custom = LoggingEtsyMonitor(monitor, logger)

        started = time.monotonic()
        print("Запуск реального цикла парсинга...", flush=True)
        results = await asyncio.get_running_loop().run_in_executor(
            None, custom.run_monitoring_cycle_with_logging
        )
        await custom.flush_logs()

        elapsed = time.monotonic() - started
        print("-" * 60)
        print(f"Завершено за {elapsed:.1f} сек")
        print(f"Записей лога: {len(logger.entries)}")
        errors = [e for e in logger.entries if any(m in e for m in ("❌", "⚠️", "🛑"))]
        print(f"Проблем в логе: {len(errors)}")
        for error in errors[:20]:
            print(f"  {error}")
        return 0
    finally:
        lock.set_stopped()
        if config_backup is not None:
            CONFIG_PATH.write_text(config_backup, encoding="utf-8")
            print("Исходный config-main.txt восстановлен", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()) or 0)
    except KeyboardInterrupt:
        print("Прервано")
        sys.exit(130)