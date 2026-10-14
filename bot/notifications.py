"""
Система уведомлений для Telegram бота
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.database import BotDatabase
from models.product import Product


def _esc(text: str) -> str:
    """Экранирование текста для HTML parse mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class ParsingLogger:
    """Живой лог парсинга в Telegram (по подходу веб-логов).

    Принцип отображения: подробные записи об успехе НЕ выводятся — за прогон их
    бывают сотни, а Telegram позволяет держать лог в одном сообщении (лимит
    ~4096 символов). Показываются только ПРОБЛЕМЫ (❌/⚠️/🛑) плюс сводный
    прогресс и итог. Благодаря этому статус парсинга виден целиком, а не
    «последние 78 строк».

    Ограничения Telegram соблюдаются так:
    * основное сообщение одно и редактируется;
    * проблемы втискиваются по бюджету символов, а не по числу строк;
    * не поместившиеся проблемы уходят отдельным сообщением в финале —
      ни один проблемный магазин не пропадает молча;
    * правки throttled по времени — чтобы не словить flood control.

    ``user_id=None`` означает автоматический запуск по расписанию: лог
    рассылается всем администраторам (как и раньше делали стартовые/финальные
    уведомления).
    """

    ERROR_MARKERS = ("❌", "⚠️", "🛑")

    def __init__(self, notification_service, user_id: Optional[int] = None):
        self.notification_service = notification_service
        self.user_id = user_id
        self.recipients: List[int] = []
        self.log_messages = {}  # recipient -> Message
        self.log_entries = []   # полный список событий (не показывается)
        self.errors = []        # полный список проблем (никогда не обрезается)
        self._shown_count = 0  # сколько проблем показано в последнем тексте
        self.ok_count = 0       # сколько успешных шагов прошло

        self.max_message_length = 4000
        # Равный интервалу флаша в scheduler: одна правка на тик, без потока
        # правок в Telegram (flood control) и без «мерцания» прогресса.
        self.min_update_interval = 5.0   # не чаще раза в 5 сек
        self.error_min_interval = 2.0    # для ошибок — не чаще раза в 2 сек

        self.total_shops = 0
        self.current_shop = 0
        self.new_products = 0
        self.stopped = False
        # Ставится монитором, когда НИ ОДИН магазин не удалось проверить.
        # Иначе финал «новых товаров не найдено» — просто враньё.
        self.all_shops_failed = False
        self.failed_shops_details = []  # (магазин, url, причина)

        self._dirty = False
        self._shown_errors = 0
        self._last_update_at = 0.0
        self._started_at: Optional[float] = None
        self._final_line: Optional[str] = None
        self._finished = False

    # ------------------------------------------------------------------ setup

    async def start_logging(self):
        """Отправляет первое сообщение всем получателям."""
        self._started_at = time.monotonic()
        text = self._build_text()
        sent = []

        if self.user_id:
            candidates = [self.user_id]
        else:
            # Автоматический запуск: лог получают все админы.
            try:
                admins = await self.notification_service.db.get_all_admins()
            except Exception as e:
                logging.error(f"Не удалось получить список админов для лога: {e}")
                admins = []
            # db отдаёт список кортежей (id, имя); терпим и просто id.
            candidates = [
                admin[0] if isinstance(admin, (tuple, list)) else admin
                for admin in (admins or [])
            ]

        for recipient in candidates:
            try:
                message = await self.notification_service.send_message_to_user(
                    recipient, text
                )
                if message:
                    self.log_messages[recipient] = message
                    sent.append(recipient)
            except Exception as e:
                logging.error(f"Не удалось отправить лог парсинга {recipient}: {e}")

        self.recipients = sent
        if sent:
            self.log_entries.append("⏳ Инициализация...")
        else:
            logging.warning(
                "Лог парсинга не отправлен ни одному получателю "
                "(парсинг продолжится без визуального лога)"
            )
        return sent

    def set_total_shops(self, total: int):
        self.total_shops = total
        self.current_shop = 0
        self._dirty = True

    def set_failed_shops(self, details):
        """Список (магазин, url, причина) для отдельного сообщения с ошибками."""
        self.failed_shops_details = list(details or [])

    # ----------------------------------------------------------------- events

    async def add_log_entry(self, entry: str):
        """Добавляет событие в лог и обновляет сообщение (с троттлингом)."""
        if not self.log_messages:
            return

        self.log_entries.append(entry)

        if entry.startswith("🔄 [") and self.total_shops > 0:
            # Прогресс по магазинам: «🔄 [7/45] Парсим: ShopName»
            self.current_shop = min(self.current_shop + 1, self.total_shops)
            self._dirty = True
        elif any(marker in entry for marker in self.ERROR_MARKERS):
            if "🛑" in entry:
                self.stopped = True
            self.errors.append(entry)
            self._dirty = True
            await self._maybe_update()
            return
        else:
            if entry.startswith("✅"):
                self.ok_count += 1
            elif entry.startswith("🎉"):
                try:
                    self.new_products = int(
                        entry.split("Найдено")[1].split("новых")[0].strip()
                    )
                except (IndexError, ValueError):
                    pass
            if "📋 Найдено" in entry or "🎉" in entry or "🧹" in entry:
                self._dirty = True

        await self._maybe_update()

    async def mark_stopped(self):
        """Парсинг остановлен принудительно."""
        self.stopped = True
        await self.add_log_entry("🛑 Парсинг остановлен принудительно")

    async def tick(self):
        """Форсирует обновление, если накопились неотправленные изменения.

        Вызывается планировщиком по таймеру — гарантирует, что запись не
        потеряется только из-за троттлинга.
        """
        await self._maybe_update()

    async def _maybe_update(self):
        """Обновляет сообщение, если прошёл нужный интервал."""
        if not self._dirty or not self.log_messages:
            return

        now = time.monotonic()
        since_last = now - self._last_update_at

        # Новые проблемы показываем быстрее, прогресс — не чаще раза в 10 сек.
        has_new_errors = len(self.errors) > self._shown_errors
        interval = self.error_min_interval if has_new_errors else self.min_update_interval

        if since_last < interval:
            return

        await self._update_message()

    # --------------------------------------------------------------- rendering

    def _top_lines(self) -> List[str]:
        if self.stopped:
            header = "🛑 <b>Парсинг Etsy</b>"
        elif self._finished:
            header = "🏁 <b>Парсинг Etsy — завершён</b>"
        else:
            header = "🔄 <b>Парсинг Etsy</b>"
        return [
            header,
            "",
            f"📊 {_esc(self._progress_text())}",
            f"⏱ {_esc(self._time_text())}",
            "",
        ]

    def _bottom_lines(self) -> List[str]:
        lines = []
        if self._final_line:
            lines += [self._final_line, ""]
        lines.append(
            f"ℹ️ Успешных шагов: {self.ok_count} · "
            f"всего событий: {len(self.log_entries)} · "
            f"в логе только проблемы"
        )
        return lines

    def _render(self) -> str:
        """Собирает текст, втискивая проблемы ВСЕ, что влезают в лимит.

        Ограничение — по символам, а не по числу строк: 60 проблем средней
        длины спокойно влезают в сообщение Telegram, обрезать их на 25 было
        бы бессмысленно. Что не влезло — уходит в продолжение в финале.

        ``self.errors`` тут НЕ изменяется: полный список нужен, чтобы
        отправить не поместившееся в продолжении. Поэтому подбор идёт строго
        по бюджету символов, а не «обрезаем, пока не влезло».
        """
        top = self._top_lines()
        bottom = self._bottom_lines()

        budget = (
            self.max_message_length
            - sum(len(line) + 1 for line in top)
            - sum(len(line) + 1 for line in bottom)
            - len("⚠️ <b>Проблемы (999)</b>") - 60  # запас на заголовок
        )

        shown = []
        used = 0
        for error in reversed(self.errors):
            cost = len(_esc(error)) + 3  # "• " + перевод строки
            if used + cost > budget:
                break
            shown.insert(0, error)
            used += cost
        self._shown_count = len(shown)

        total_errors = len(self.errors)
        hidden = total_errors - len(shown)

        lines = list(top)
        if shown:
            lines.append(f"⚠️ <b>Проблемы ({total_errors})</b>")
            lines += [f"• {_esc(error)}" for error in shown]
            if hidden > 0:
                lines.append(f"• …и ещё {hidden} — полный список ниже отдельным сообщением")
            lines.append("")
        else:
            lines += ["✅ Проблем нет", ""]

        lines += bottom
        return "\n".join(lines)

    def _build_text(self) -> str:
        return self._render()

    def _progress_text(self) -> str:
        if self.total_shops:
            shops = f"{self.current_shop}/{self.total_shops} магазинов"
        else:
            shops = "магазины не определены"
        return f"{shops} · новых товаров: {self.new_products}"

    def _time_text(self) -> str:
        now = datetime.now().strftime("%H:%M")
        if self._started_at is None:
            return now
        elapsed = int(time.monotonic() - self._started_at)
        if elapsed < 60:
            return f"{now} · идёт {elapsed} сек"
        return f"{now} · идёт {elapsed // 60} мин"

    async def _update_message(self):
        text = self._build_text()
        self._dirty = False
        self._shown_errors = len(self.errors)
        self._last_update_at = time.monotonic()

        for recipient, message in self.log_messages.items():
            try:
                await self.notification_service.edit_message(
                    recipient, message.message_id, text
                )
            except Exception as e:
                logging.error(f"Не удалось обновить лог парсинга {recipient}: {e}")

    async def finish_logging(self, total_new_products: int, failed: bool = False):
        """Финальная строка + гарантированная отправка итогового статуса.

        ``failed=True`` — прогон упал до конца, «новых товаров не найдено»
        здесь было бы враньём, поэтому статус другой.
        """
        self.new_products = total_new_products
        self._finished = True

        if failed:
            self._final_line = "❌ <b>Парсинг завершился с ошибкой</b>"
        elif self.all_shops_failed:
            self._final_line = (
                "❌ <b>Не удалось проверить ни одного магазина</b> — "
                "список проблем выше"
            )
        elif total_new_products > 0:
            self._final_line = (
                f"✅ <b>Парсинг завершён!</b> Найдено {total_new_products} "
                f"новых товаров"
            )
        elif self.stopped:
            self._final_line = "🛑 <b>Парсинг прерван</b> пользователем"
        else:
            self._final_line = "✅ <b>Парсинг завершён!</b> Новых товаров не найдено"

        self._dirty = True

        # Финальный статус обязан быть виден: обновление здесь принудительное.
        if self.log_messages:
            await self._update_message()
            await self._send_error_report()

    def _error_report_lines(self) -> List[str]:
        """Строки отдельного отчёта: сломанные магазины со ссылками."""
        lines = []
        listed = set()

        for shop, url, reason in self.failed_shops_details:
            if shop in listed:
                continue
            listed.add(shop)
            name = f'<a href="{url}">{_esc(shop)}</a>' if url else _esc(shop)
            lines.append(f"• {name} — {_esc(reason)}")

        # Ошибки, не привязанные к конкретному магазину (сбой цикла, стоп и т.п.)
        for entry in self.errors:
            if any(shop in entry for shop in listed):
                continue
            lines.append(f"• {_esc(entry)}")
            listed.add(entry)

        return lines

    async def _send_error_report(self):
        """Отдельное сообщение со списком проблемных магазинов.

        Лог живёт в одном сообщении и показывает срез; полный список с
        кликабельными ссылками на магазины уходит отдельным сообщением —
        его удобно пролистать и потом вручную открыть каждый магазин.
        """
        lines = self._error_report_lines()
        if not lines or not self.log_messages:
            return

        chunks = []
        current = []
        used = 0
        # Запас под заголовок и подвал.
        for line in lines:
            cost = len(line) + 3
            if used + cost > self.max_message_length - 300:
                chunks.append(current)
                current, used = [], 0
            current.append(line)
            used += cost
        if current:
            chunks.append(current)

        total = len(lines)
        for index, chunk in enumerate(chunks, 1):
            header = f"🚫 <b>Магазины с ошибками ({total})</b>"
            if len(chunks) > 1:
                header += f" — часть {index}/{len(chunks)}"
            body = "\n".join(chunk)
            text = (
                f"{header}\n\n"
                f"Проверить эти магазины вручную не получилось — "
                f"список один на весь прогон.\n\n{body}\n\n"
                f"ℹ️ Это итоговый список, не срез лога."
            )

            for recipient in self.recipients:
                try:
                    await self.notification_service.send_message_to_user(
                        recipient, text
                    )
                except Exception as e:
                    logging.error(
                        f"Не удалось отправить список магазинов с ошибками {recipient}: {e}"
                    )
                await asyncio.sleep(0.4)

class NotificationService:
    """Сервис для отправки уведомлений о новых товарах"""
    
    def __init__(self, bot: Bot, db: BotDatabase):
        self.bot = bot
        self.db = db
        self.max_message_length = 4000  # Лимит Telegram ~4096, оставляем запас
    
    async def send_long_message(self, user_id: int, message: str, parse_mode: str = "HTML") -> bool:
        """Отправка длинного сообщения с разбивкой на части"""
        if len(message) <= self.max_message_length:
            return await self.send_message_to_user(user_id, message, parse_mode)
        
        # Разбиваем сообщение на части
        parts = []
        current_part = ""
        
        for line in message.split('\n'):
            if len(current_part) + len(line) + 1 > self.max_message_length:
                if current_part:
                    parts.append(current_part)
                    current_part = line + '\n'
                else:
                    # Если одна строка слишком длинная, обрезаем её
                    parts.append(line[:self.max_message_length])
            else:
                current_part += line + '\n'
        
        if current_part:
            parts.append(current_part)
        
        # Отправляем части по очереди
        success = True
        for i, part in enumerate(parts):
            if i == 0:
                result = await self.send_message_to_user(user_id, part, parse_mode)
            else:
                part_with_header = f"📊 <b>Продолжение ({i+1}/{len(parts)})</b>\n\n{part}"
                result = await self.send_message_to_user(user_id, part_with_header, parse_mode)
            
            if not result:
                success = False
            
            # Небольшая задержка между сообщениями
            await asyncio.sleep(0.5)
        
        return success
    
    async def send_message_to_user(self, user_id: int, message: str, parse_mode: str = "HTML") -> bool:
        """Отправка сообщения конкретному пользователю"""
        try:
            sent_message = await asyncio.wait_for(
                self.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=parse_mode
                ),
                timeout=30
            )
            return sent_message
        except asyncio.TimeoutError:
            logging.error(f"Таймаут отправки сообщения пользователю {user_id}")
            return False
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
            return False

    async def edit_message(self, chat_id: int, message_id: int, new_text: str, parse_mode: str = "HTML") -> bool:
        """Редактирование существующего сообщения"""
        try:
            await asyncio.wait_for(
                self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=new_text,
                    parse_mode=parse_mode
                ),
                timeout=30
            )
            return True
        except asyncio.TimeoutError:
            logging.error(f"Таймаут редактирования сообщения")
            return False
        except Exception as e:
            logging.error(f"Ошибка редактирования сообщения: {e}")
            return False
    
    async def send_new_product_notification(self, product: Product) -> bool:
        """Отправка уведомления о новом товаре всем администраторам"""
        try:
            # Получаем список всех администраторов
            admins = await self.db.get_all_admins()
            
            if not admins:
                logging.warning("Нет администраторов для отправки уведомлений")
                return False
            
            # Формируем сообщение
            message_text = self._format_notification_message(product)
            
            # Отправляем уведомление каждому администратору
            sent_count = 0
            for admin_id, _ in admins:
                try:
                    await asyncio.wait_for(
                        self.bot.send_message(
                            chat_id=admin_id,
                            text=message_text,
                            parse_mode="HTML",
                            disable_web_page_preview=False
                        ),
                        timeout=30
                    )
                    sent_count += 1
                    logging.info(f"Уведомление отправлено администратору {admin_id}")
                    
                    # Задержка между сообщениями
                    await asyncio.sleep(0.5)
                    
                except asyncio.TimeoutError:
                    logging.error(f"Таймаут отправки администратору {admin_id}")
                except TelegramForbiddenError:
                    logging.warning(f"Администратор {admin_id} заблокировал бота")
                except TelegramBadRequest as e:
                    logging.error(f"Ошибка отправки администратору {admin_id}: {e}")
                except Exception as e:
                    logging.error(f"Неожиданная ошибка при отправке администратору {admin_id}: {e}")
            
            logging.info(f"Уведомление отправлено {sent_count} из {len(admins)} администраторов")
            return sent_count > 0
            
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления о товаре: {e}")
            return False
    
    async def send_multiple_products_notification(self, products: List[Product]) -> bool:
        """Отправка уведомления о нескольких новых товарах"""
        if not products:
            return False
        
        try:
            # Получаем список всех администраторов
            admins = await self.db.get_all_admins()
            
            if not admins:
                logging.warning("Нет администраторов для отправки уведомлений")
                return False
            
            # Группируем товары по магазинам
            shops_products = {}
            for product in products:
                if product.shop_name not in shops_products:
                    shops_products[product.shop_name] = []
                shops_products[product.shop_name].append(product)
            
            # Отправляем уведомления по магазинам
            sent_count = 0
            for shop_name, shop_products in shops_products.items():
                message_text = self._format_multiple_products_message(shop_name, shop_products)
                
                # Отправляем каждому администратору с поддержкой длинных сообщений
                for admin_id, _ in admins:
                    try:
                        success = await self.send_long_message(admin_id, message_text)
                        if success:
                            sent_count += 1
                        
                    except Exception as e:
                        logging.error(f"Неожиданная ошибка при отправке администратору {admin_id}: {e}")
            
            logging.info(f"Уведомления о {len(products)} товарах отправлены")
            return sent_count > 0
            
        except Exception as e:
            logging.error(f"Ошибка отправки уведомлений о товарах: {e}")
            return False
    
    def _format_notification_message(self, product: Product) -> str:
        """Форматирование минимального сообщения о новом товаре согласно требованиям"""
        discovery_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        # Минимальное сообщение: заголовок, магазин, ссылка, время
        message = f"""🆕 <b>Найден новый товар в {product.shop_name}</b>

<a href="{product.url}">{product.title[:80]}{'...' if len(product.title) > 80 else ''}</a>

🕐 {discovery_time}"""
        
        return message
    
    def _format_multiple_products_message(self, shop_name: str, products: List[Product]) -> str:
        """Форматирование минимального сообщения о нескольких новых товарах"""
        discovery_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        message = f"""🆕 <b>Найдено {len(products)} новых товаров в {shop_name}</b>

🕐 {discovery_time}

"""
        
        # Показываем максимум 5 товаров в минимальном формате
        for i, product in enumerate(products[:5]):
            title = product.title[:60] + '...' if len(product.title) > 60 else product.title
            message += f"• <a href='{product.url}'>{title}</a>\n"
        
        if len(products) > 5:
            message += f"\n... и еще {len(products) - 5} товаров"
        
        return message
    
    async def send_parsing_started_notification(self, user_id: int = None) -> bool:
        """Уведомление о начале парсинга (только инициатору или всем админам)"""
        try:
            message = f"""🚀 <b>Запуск парсинга</b>

🔍 Начинаю поиск новых товаров..."""
            
            if user_id:
                # Отправляем только инициатору
                return await self.send_message_to_user(user_id, message)
            else:
                # Отправляем всем админам (автоматический запуск по расписанию)
                admins = await self.db.get_all_admins()
                if not admins:
                    return False
                
                for admin_id, _ in admins:
                    await self.send_message_to_user(admin_id, message)
                
                return True
            
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления о начале парсинга: {e}")
            return False
    
    async def send_parsing_completed_notification(self, total_new_products: int, user_id: int = None) -> bool:
        """Уведомление о завершении парсинга (только инициатору или всем админам)"""
        try:
            if total_new_products > 0:
                message = f"""✅ <b>Парсинг завершен</b>

🆕 Найдено новых товаров: {total_new_products}"""
            else:
                message = f"""✅ <b>Парсинг завершен</b>

📭 Новых товаров не найдено"""
            
            if user_id:
                # Отправляем только инициатору
                return await self.send_message_to_user(user_id, message)
            else:
                # Отправляем всем админам (автоматический запуск по расписанию)
                admins = await self.db.get_all_admins()
                if not admins:
                    return False
                
                for admin_id, _ in admins:
                    await self.send_message_to_user(admin_id, message)
                
                return True
            
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления о завершении парсинга: {e}")
            return False
    
    def _format_notification_message(self, product: Product) -> str:
        """Форматирование минимального сообщения о новом товаре согласно требованиям"""
        discovery_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        # Минимальное сообщение: заголовок, магазин, ссылка, время
        message = f"""🆕 <b>Найден новый товар в {product.shop_name}</b>

<a href="{product.url}">{product.title[:80]}{'...' if len(product.title) > 80 else ''}</a>

🕐 {discovery_time}"""
        
        return message
    
    def _format_multiple_products_message(self, shop_name: str, products: List[Product]) -> str:
        """Форматирование сообщения о нескольких новых товарах"""
        discovery_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        message = f"""🆕 <b>Найдено {len(products)} новых товаров в {shop_name}</b>

🕐 {discovery_time}

"""
        
        # Показываем все товары, но с ограничением длины
        for i, product in enumerate(products):
            title = product.title[:60] + '...' if len(product.title) > 60 else product.title
            message += f"• <a href='{product.url}'>{title}</a>\n"
        
        return message
    
    async def send_parsing_started_notification(self, user_id: int = None) -> bool:
        """Уведомление о начале парсинга (только инициатору или всем админам)"""
        try:
            message = f"""🚀 <b>Запуск парсинга</b>

🔍 Начинаю поиск новых товаров..."""
            
            if user_id:
                # Отправляем только инициатору
                return await self.send_message_to_user(user_id, message)
            else:
                # Отправляем всем админам (автоматический запуск по расписанию)
                admins = await self.db.get_all_admins()
                if not admins:
                    return False
                
                for admin_id, _ in admins:
                    await self.send_message_to_user(admin_id, message)
                
                return True
            
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления о начале парсинга: {e}")
            return False
    
    async def send_parsing_completed_notification(self, total_new_products: int, user_id: int = None) -> bool:
        """Уведомление о завершении парсинга (только инициатору или всем админам)"""
        try:
            if total_new_products > 0:
                message = f"""✅ <b>Парсинг завершен</b>

🆕 Найдено новых товаров: {total_new_products}"""
            else:
                message = f"""✅ <b>Парсинг завершен</b>

📭 Новых товаров не найдено"""
            
            if user_id:
                # Отправляем только инициатору
                return await self.send_message_to_user(user_id, message)
            else:
                # Отправляем всем админам (автоматический запуск по расписанию)
                admins = await self.db.get_all_admins()
                if not admins:
                    return False
                
                for admin_id, _ in admins:
                    await self.send_message_to_user(admin_id, message)
                
                return True
            
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления о завершении парсинга: {e}")
            return False
