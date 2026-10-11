"""Сухой старт бота против локального фейк-Bot-API.

Проверяет, что бот целиком поднимается на свежем aiogram 3.31: импорт
обработчиков, создание планировщиков, стартовое уведомление админам.
Никаких реальных сообщений никому не уходит — вместо api.telegram.org
используется локальный сервер.

Запуск: python scripts\bot_dry_boot.py
"""
import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote_plus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

CALLS = []
RUN_SECONDS = 25.0


class FakeTelegramAPI(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, payload):
        method = self.path.rstrip("/").split("/")[-1]
        CALLS.append(method)

        if method == "getMe":
            self._reply({"ok": True, "result": {
                "id": 1, "is_bot": True, "first_name": "Etsy",
                "username": "etsy_parser_dry_bot", "can_join_groups": True,
                "can_read_all_group_messages": False, "supports_inline_queries": False,
            }})
        elif method == "getUpdates":
            # Пустая лента: бот просто «слушает» и не отвечает ни на что.
            self._reply({"ok": True, "result": []})
        elif method == "sendMessage":
            self._reply({"ok": True, "result": {
                "message_id": len(CALLS), "date": int(time.time()),
                "chat": {"id": int(payload.get("chat_id", 0)), "type": "private"},
                "text": payload.get("text", ""),
            }})
        else:
            self._reply({"ok": True, "result": {
                "message_id": 1, "date": int(time.time()),
                "chat": {"id": int(payload.get("chat_id", 0)), "type": "private"},
                "text": payload.get("text", ""),
            }})

    def do_GET(self):
        self._handle({})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        payload = {}
        for part in raw.split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
                payload[unquote_plus(key)] = unquote_plus(value)
        self._handle(payload)


async def main():
    server = HTTPServer(("127.0.0.1", 0), FakeTelegramAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    print(f"Фейковый Bot API: 127.0.0.1:{port}")

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer
    from aiogram.enums import ParseMode

    import bot.main as bot_main

    api = TelegramAPIServer(
        base=f"http://127.0.0.1:{port}/bot{{token}}/{{method}}",
        file=f"http://127.0.0.1:{port}/file/bot{{token}}/{{path}}",
    )

    # aiogram валидирует формат токена, поэтому нужен синтаксически
    # корректный фейк (пример из документации Telegram). Он не уходит в сеть:
    # base указывает на локальный сервер.
    real_bot = Bot(
        token="1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
        session=AiohttpSession(api=api),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Подменяем фабрику Bot: дальше бот работает как обычно, но в фейк-API.
    bot_main.Bot = lambda **kwargs: real_bot

    print(f"Запускаю bot.main.main() на {RUN_SECONDS} сек...\n" + "-" * 60)
    try:
        await asyncio.wait_for(bot_main.main(), timeout=RUN_SECONDS)
    except asyncio.TimeoutError:
        print("-" * 60)
        print(f"Бот отработал {RUN_SECONDS} сек и был остановлен (ожидаемо)")
    except Exception as e:
        print("-" * 60)
        print(f"ОШИБКА СТАРТА: {type(e).__name__}: {e}")
        return 1
    finally:
        await real_bot.session.close()
        server.shutdown()

    print("-" * 60)
    print(f"Вызовов к Bot API: {len(CALLS)}")
    for method in sorted(set(CALLS)):
        print(f"  {method}: {CALLS.count(method)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)