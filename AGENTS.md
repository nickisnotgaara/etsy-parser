# AGENTS.md — Etsy Parser

## Как запустить

Готового venv в репозитории не было. Зависимости ставятся так:

```
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install aiogram aiosqlite python-dotenv schedule pytz \
    openpyxl selenium selenium-stealth blinker beautifulsoup4 requests lxml certifi \
    google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client \
    gspread pandas
```

**Не используй пины из `requirements.txt`.** Они рассчитаны на Python 3.9–3.11:
`pandas==2.1.3` и `aiohttp 3.9` (через `aiogram==3.4.1`) не имеют wheels под 3.14
и собираются из исходников. Стави без версий — под 3.14 ставится
aiogram 3.31 / selenium 4.51 / pandas 3.0.6.

Внимание при работе с aiogram 3.31: `AiohttpSession(api_url=...)` больше не
принимается, адрес задаётся через `TelegramAPIServer(base=..., file=...)`.

## Креденшелов в репозитории нет

`config-main.txt` в корне и в `dist/` — **заглушки** (`is_working=stop`).
Файла `.env` не существует. Отсюда берутся:

| Параметр | Где |
|---|---|
| `BOT_TOKEN`, `ADMIN_ID` | `config-main.txt` |
| `EVERBEE_USERNAME`, `EVERBEE_PASSWORD` | `config-main.txt` (читает `utils/everbee_client.py`) |
| доступ к таблице магазинов | `credentials.json` (Google Sheets, этот файл в репозитории есть) |

Значения пишет GUI при первом запуске собранного `.exe`. Без `BOT_TOKEN`
бота запустить нельзя; без `EVERBEE_*` парсинг проходит по всем магазинам и
на каждом падает с «Не удалось получить валидный токен».

## Проверка без креденшелов

- `tests\test_parsing_logger.py` — рендеринг лога парсинга (офлайн).
- `tests\test_live_parsing_log.py` — что event loop не блокируется парсингом.
- `tests\test_aiogram_integration.py` — настоящий клиент aiogram против
  локального фейк-Bot-API на 127.0.0.1, никого не спамит.
- `scripts\bot_dry_boot.py` — сухой старт всего бота (обработчики, оба
  планировщика, стартовые уведомления) против локального фейк-Bot-API.
- `scripts\live_parse_no_telegram.py` — реальный парсинг Etsy в консоль.
- `scripts\render_log_preview.py` — прогоняет реальный лог через рендерер.

Все требуют `$env:PYTHONIOENCODING='utf-8'`, иначе консоль Windows (cp1251)
падает на первом же `print` с эмодзи.

## Мелочи, которые кусаются

- Парсинг не стартует, пока в `config-main.txt` `is_working=stop`: `is_parser_working()`
  трактует это как «остановлено пользователем» и цикл выходит сразу. Бот ставит
  `is_working=start` сам через `ParserLock.set_working()`.
- `output/parsing/<дата>_<время>/` создаётся в начале каждого прогона и удаляется
  только успешным `cleanup_output_folder()` — после упавшего прогона папки остаются.
- В `bot/notifications.py` четыре метода определены по два раза
  (`_format_notification_message`, `_format_multiple_products_message`,
  `send_parsing_started_notification`, `send_parsing_completed_notification`);
  вторые перетирают первые.