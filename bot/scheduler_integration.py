"""
Интеграция планировщика с Telegram ботом
"""
import asyncio
import logging
import schedule
import time
import pytz
import os
from datetime import datetime, timezone
from threading import Thread
import threading
from typing import Optional

from bot.database import BotDatabase
from bot.notifications import NotificationService
from core.monitor import EtsyMonitor
from models.product import Product
import os

class ParserLock:
    """Класс для управления блокировкой парсера через config-main.txt"""
    
    def __init__(self):
        self.config_file = "config-main.txt"
    
    def is_running(self) -> bool:
        """Проверяет, запущен ли парсер согласно config-main.txt"""
        from config.settings import is_parser_working
        return is_parser_working()
    
    def set_working(self):
        """Устанавливает статус 'start' в config-main.txt"""
        self._update_config_value("is_working", "start")
    
    def set_stopped(self):
        """Устанавливает статус 'stop' в config-main.txt"""
        self._update_config_value("is_working", "stop")
    
    def get_status(self) -> str:
        """Получает текущий статус из config-main.txt"""
        from config.settings import read_config_file
        config_data = read_config_file()
        return config_data.get('is_working', 'stop')
    
    def _update_config_value(self, key: str, value: str):
        """Обновляет значение в config-main.txt (сохраняет все остальные параметры)"""
        try:
            # Читаем все существующие параметры
            existing_data = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and '=' in line and not line.startswith('#'):
                            k, v = line.split('=', 1)
                            existing_data[k.strip()] = v.strip()
            
            # Обновляем нужный ключ
            existing_data[key] = value
            
            # Записываем все обратно
            with open(self.config_file, 'w', encoding='utf-8') as f:
                for k, v in existing_data.items():
                    f.write(f"{k}={v}\n")
                
        except Exception as e:
            logging.error(f"Ошибка обновления config-main.txt: {e}")
    
    def force_stop(self):
        """Принудительно останавливает парсер"""
        try:
            self.set_stopped()
            logging.info("Парсер принудительно остановлен")
            return True
        except Exception as e:
            logging.error(f"Ошибка принудительной остановки: {e}")
            return False
    
    def reset_if_stuck(self, timeout_minutes: int = 30):
        """Сбрасывает блокировку если парсер завис"""
        try:
            if not os.path.exists(self.config_file):
                return False
            
            # Проверяем время последнего изменения файла
            file_time = os.path.getmtime(self.config_file)
            current_time = time.time()
            
            # Если файл не менялся больше timeout_minutes и статус 'start'
            if (current_time - file_time) > (timeout_minutes * 60) and self.is_running():
                self.set_stopped()
                logging.warning(f"Сброшена зависшая блокировка парсера (timeout: {timeout_minutes} мин)")
                return True
            
            return False
        except Exception as e:
            logging.error(f"Ошибка проверки зависшей блокировки: {e}")
            return False

class LoggingEtsyMonitor:
    """Обертка для EtsyMonitor с поддержкой логирования"""
    
    def __init__(self, monitor: EtsyMonitor, logger=None):
        self.monitor = monitor
        self.logger = logger
        self.pending_logs = []
        self._log_lock = threading.Lock()

    def log_sync(self, message: str):
        """Добавляет запись в лог синхронно"""
        if self.logger:
            with self._log_lock:
                self.pending_logs.append(message)
            logging.info(f"LOG: {message}")  # Дублируем в обычные логи
    
    async def flush_logs(self):
        """Отправляет накопленные логи в Telegram и чистит буфер."""
        if not self.logger:
            return

        with self._log_lock:
            batch, self.pending_logs = self.pending_logs, []

        for log_message in batch:
            try:
                await self.logger.add_log_entry(log_message)
            except Exception as e:
                logging.error(f"Ошибка отправки лога: {e}")
    
    def run_monitoring_cycle_with_logging(self):
        """Запуск мониторинга с логированием без двойного парсинга"""
        try:
            # Получаем ссылки. data_service сам обрабатывает fallback на
            # локальный кэш (output/shops_cache.json), если Google Sheets
            # недоступен. Здесь мы только решаем, что делать с результатом.
            links = self.monitor.data_service.load_shop_urls()

            if not links:
                # Ни GSheets, ни локальный кэш не дали результата.
                # Раньше здесь всегда был GSheets — теперь сообщение
                # уточняет, что fallback тоже не сработал.
                self.log_sync(
                    "❌ Не удалось получить список магазинов: "
                    "Google Sheets недоступен и локальный кэш пуст. "
                    "Проверьте credentials.json / доступ к таблице. "
                    "Парсинг пропущен."
                )
                return []

            # Если load_shop_urls() отработал через fallback, в логах выше уже
            # будет сообщение "♻️ Используем кэшированный список". Здесь просто
            # сообщаем пользователю, сколько магазинов пошло в работу.
            self.log_sync(f"📋 Найдено {len(links)} магазинов для парсинга")
            
            # Устанавливаем общее количество магазинов в логгере
            if self.logger:
                self.logger.set_total_shops(len(links))
            
            # Создаём папку для текущего сеанса парсинга
            parsing_dir = self.monitor.data_service.start_parsing_session()
            
            # Парсим все магазины с логированием
            all_shop_products = self.parse_all_shops_with_logging(links)
            
            # Проверяем, не был ли парсинг остановлен принудительно
            from config.settings import is_parser_working
            if not is_parser_working():
                self.log_sync("🛑 Парсинг был остановлен принудительно")
                return []
            
            if not all_shop_products:
                self.log_sync("❌ Не удалось получить данные ни от одного магазина")
                return []
            
            self.log_sync("🔍 Анализируем новые товары...")
            
            # Сохраняем результаты в JSON
            results_file = self.monitor.data_service.save_results_to_json(all_shop_products)
            
            # Сравниваем с предыдущими результатами
            current_results = {}
            for shop_name, products in all_shop_products.items():
                current_results[shop_name] = {product.listing_id: product.url for product in products}
            
            # Находим новые товары
            new_products_dict = self.monitor.data_service.compare_all_shops_results(current_results)
            
            # Анализируем новые товары через EverBee
            if new_products_dict:
                logging.info(f"\n=== АНАЛИЗ НОВЫХ ТОВАРОВ ЧЕРЕЗ EVERBEE ===")
                logging.info(f"📦 Товаров для анализа: {len(new_products_dict)}")
                self.monitor.tops_service.process_new_products(
                    new_products_dict, 
                    self.monitor.data_service.current_parsing_folder
                )
            
            # Сохраняем финальные результаты
            final_results_file = self.monitor.data_service.save_results_with_new_products(all_shop_products, new_products_dict)
            
            # Формируем результаты для бота
            comparison_results = []
            
            for shop_name, products in all_shop_products.items():
                # Находим новые товары для этого магазина
                new_products_for_shop = []
                for product in products:
                    if product.listing_id in new_products_dict:
                        new_products_for_shop.append(product)
                
                # Создаем объект сравнения
                from models.product import ShopComparison
                comparison = ShopComparison(
                    shop_name=shop_name,
                    new_products=new_products_for_shop,
                    removed_products=[],
                    total_current=len(products),
                    total_previous=len(products) - len(new_products_for_shop),
                    comparison_date=None
                )
                
                comparison_results.append(comparison)
            
            # Логируем итоги
            total_new = len(new_products_dict)
            if total_new > 0:
                self.log_sync(f"🎉 Найдено {total_new} новых товаров!")
            else:
                self.log_sync("📭 Новых товаров не найдено")
            
            # Очищаем всю output папку
            if self.monitor.data_service.cleanup_output_folder():
                self.log_sync("🧹 Очистка завершена")
            
            return comparison_results
            
        except Exception as e:
            self.log_sync(f"❌ Критическая ошибка: {str(e)[:100]}")
            logging.error(f"Критическая ошибка в мониторинге: {e}")
            return []
    
    def parse_all_shops_with_logging(self, urls):
        """Парсит все магазины в одном браузере по очереди"""
        all_shop_products = {}
        
        for i, url in enumerate(urls, 1):
            # Проверяем, не был ли парсинг остановлен принудительно
            from config.settings import is_parser_working
            if not is_parser_working():
                self.log_sync("🛑 Парсинг остановлен пользователем")
                break
            
            try:
                shop_name = self.monitor.parser.get_shop_name_from_url(url)
                self.log_sync(f"🔄 [{i}/{len(urls)}] Парсим: {shop_name}")
                
                # Парсим магазин (только первую страницу)
                products = self.monitor.parser.parse_shop_page(url)
                
                if products:
                    all_shop_products[shop_name] = products
                    
                    # Сохраняем данные
                    filename = self.monitor.data_service.save_products_to_excel(products, shop_name)
                    
                    self.log_sync(f"✅ {shop_name}: {len(products)} товаров (первая страница)")
                else:
                    self.log_sync(f"⚠️ {shop_name}: не удалось получить товары")
                
            except Exception as e:
                shop_name = self.monitor.parser.get_shop_name_from_url(url) if url else "Unknown"
                self.log_sync(f"❌ Ошибка в {shop_name}: {str(e)[:50]}")
                logging.error(f"Ошибка парсинга {url}: {e}")
        
        # Закрываем браузер после всех магазинов
        if hasattr(self.monitor.parser, 'close_browser'):
            self.monitor.parser.close_browser()
            self.log_sync("🔄 Браузер закрыт")
        
        return all_shop_products



class BotScheduler:
    """Планировщик с интеграцией Telegram бота"""

    # Как часто накопленные записи парсинга уходят в Telegram-лог.
    LOG_FLUSH_INTERVAL = 5.0

    def __init__(self, notification_service: NotificationService, db: BotDatabase):
        self.notification_service = notification_service
        self.db = db
        self.monitor = EtsyMonitor()
        self.is_running = False
        self.scheduler_thread: Optional[Thread] = None
        self.moscow_tz = pytz.timezone('Europe/Moscow')
        self.parser_lock = ParserLock()
        self.main_loop = None  # Ссылка на основной event loop
    
    async def scheduled_parsing_job(self, user_id: int = None):
        """Задача парсинга с уведомлениями"""
        logger = None
        
        # Проверяем и сбрасываем зависшие блокировки
        self.parser_lock.reset_if_stuck()
        
        # Проверяем блокировку парсера
        if self.parser_lock.is_running():
            error_msg = "⚠️ Парсер уже запущен! Дождитесь завершения текущего процесса."
            if user_id:
                await self.notification_service.send_message_to_user(user_id, error_msg)
            logging.warning("Попытка запуска парсера во время работы другого процесса")
            return
        
        # Устанавливаем блокировку
        self.parser_lock.set_working()
        
        try:
            logging.info("Запуск парсинга с уведомлениями")
            
            # Живой лог нужен и при ручном, и при автоматическом запуске.
            # При user_id=None ParsingLogger сам рассылает его всем админам —
            # раньше автоматический прогон шёл вообще без лога.
            from bot.notifications import ParsingLogger
            logger = ParsingLogger(self.notification_service, user_id)
            await logger.start_logging()

            # Запускаем мониторинг с логированием
            comparison_results = await self.run_monitoring_with_logging(logger)
            
            # Собираем все новые товары
            all_new_products = []
            for result in comparison_results:
                if result.has_changes:
                    all_new_products.extend(result.new_products)
            
            # Отправляем уведомления о новых товарах (всегда всем админам)
            if all_new_products:
                await self.notification_service.send_multiple_products_notification(all_new_products)
                logging.info(f"Найдено и отправлено уведомлений о {len(all_new_products)} новых товарах")
            
            # Завершаем логирование — итог уходит в то же сообщение с логом
            await logger.finish_logging(len(all_new_products))
            
            logging.info("Парсинг завершен успешно")
            
        except Exception as e:
            logging.error(f"Ошибка в парсинге: {e}")

            # Уведомляем об ошибке
            try:
                if logger:
                    await logger.add_log_entry(f"❌ Ошибка: {str(e)[:100]}")
                    # Лог мог не успеть обновиться — показываем итог сразу,
                    # иначе статус парсинга останется неясным.
                    await logger.finish_logging(0, failed=True)
                else:
                    error_message = f"""❌ <b>Ошибка парсинга</b>

🚨 Ошибка: {str(e)[:200]}

Проверьте логи для получения подробной информации."""
                    if user_id:
                        await self.notification_service.send_message_to_user(user_id, error_message)
                    else:
                        # Отправляем всем админам
                        admins = await self.db.get_all_admins()
                        for admin_id, _ in admins:
                            await self.notification_service.send_message_to_user(admin_id, error_message)
            except Exception:
                pass
        finally:
            # Снимаем блокировку
            self.parser_lock.set_stopped()
            logging.info("Блокировка парсера снята")
    
    async def run_monitoring_with_logging(self, logger=None):
        """Запуск мониторинга с детальным логированием"""
        try:
            if logger:
                await logger.add_log_entry("🔍 Начинаем сканирование магазинов...")
            
            # Используем реальный EtsyMonitor с логированием
            comparison_results = await self.run_real_monitoring_with_logging(logger)
            
            return comparison_results
            
        except Exception as e:
            if logger:
                await logger.add_log_entry(f"❌ Критическая ошибка: {str(e)[:100]}")
            raise e
    
    async def run_real_monitoring_with_logging(self, logger=None):
        """Запуск реального мониторинга с живым логом в Telegram.

        Парсинг идёт в отдельном потоке и занимает минуты, поэтому event loop
        бота НЕЛЬЗЯ блокировать на всё это время — иначе асинхронные задачи
        (включая сам лог) не выполнятся, и пользователь увидит лог только
        постфактум. Поэтому ждём результат через ``run_in_executor``, а лог
        подливается фоновой задачей каждые несколько секунд.
        """
        custom_monitor = LoggingEtsyMonitor(self.monitor, logger)
        flusher = None

        async def flush_loop():
            """Периодически отправляет накопленные записи лога."""
            while True:
                await asyncio.sleep(self.LOG_FLUSH_INTERVAL)
                try:
                    await custom_monitor.flush_logs()
                    if logger:
                        # Гарантируем, что неотправленные изменения уедут
                        # даже если троттлинг не дал обновить сообщение.
                        await logger.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logging.error(f"Ошибка фоновой отправки лога: {e}")

        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures

            if logger:
                flusher = asyncio.create_task(flush_loop())

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(custom_monitor.run_monitoring_cycle_with_logging)
                # await, а не future.result(): event loop остаётся свободным.
                # Отдельный executor для ожидания: не зависим от того, занят
                # ли чужой пул потоков бота.
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as waiter:
                    comparison_results = await loop.run_in_executor(waiter, future.result)

            # Отправляем всё, что осталось в буфере
            await custom_monitor.flush_logs()

            return comparison_results

        except Exception as e:
            logging.error(f"Ошибка в реальном мониторинге: {e}")
            if logger:
                await logger.add_log_entry(f"❌ Ошибка мониторинга: {str(e)[:100]}")
            return []
        finally:
            if flusher:
                flusher.cancel()
                try:
                    await flusher
                except (asyncio.CancelledError, Exception):
                    pass
    
    def extract_shop_name(self, url: str) -> str:
        """Извлекает название магазина из URL"""
        try:
            if 'etsy.com/shop/' in url:
                return url.split('/shop/')[1].split('/')[0].split('?')[0]
            else:
                return url.split('//')[1].split('/')[0][:20]
        except:
            return "Unknown Shop"
    

    
    def _schedule_job_wrapper(self):
        """Обертка для запуска асинхронной задачи в синхронном планировщике.

        Планировщик schedule работает в отдельном потоке, поэтому асинхронную
        задачу нужно отправлять в основной event loop бота через
        ``asyncio.run_coroutine_threadsafe``. Создавать НОВЫЙ event loop здесь
        нельзя: внутренний aiohttp-сессия aiogram ``Bot`` привязана к loop'у,
        в котором бот был запущен. Попытка вызвать ``bot.send_message`` из
        другого loop приводит к ``RuntimeError: Event loop is closed``.
        """
        try:
            if self.main_loop and not self.main_loop.is_closed():
                # Используем основной event loop бота
                future = asyncio.run_coroutine_threadsafe(
                    self.scheduled_parsing_job(), self.main_loop
                )
                future.result()  # Ждем завершения
            else:
                logging.error(
                    "Планировщик не может запустить задачу парсинга: "
                    "основной event loop бота недоступен или закрыт. "
                    "Пропускаю запуск до следующего цикла."
                )

        except Exception as e:
            logging.error(f"Ошибка в обертке планировщика: {e}")
    
    async def update_schedule(self):
        """Обновление расписания с умным расчетом времени"""
        try:
            schedule.clear()

            schedule_time, schedule_day = await self.db.get_scheduler_settings()
            moscow_time = datetime.now(self.moscow_tz)
            current_weekday = moscow_time.strftime('%A').lower()

            # Конвертируем «МСК день + время» → «локальное время системы».
            # Без этого .monday.at("08:00") сработает в 08:00 ЛОКАЛЬНО,
            # что на сервере с TZ != Europe/Moscow ≠ 08:00 МСК.
            from utils.moscow_schedule import moscow_to_local_fire
            local_day, local_hhmm = moscow_to_local_fire(schedule_time, schedule_day)

            day_mapping = {
                "monday": schedule.every().monday,
                "tuesday": schedule.every().tuesday,
                "wednesday": schedule.every().wednesday,
                "thursday": schedule.every().thursday,
                "friday": schedule.every().friday,
                "saturday": schedule.every().saturday,
                "sunday": schedule.every().sunday
            }

            if local_day in day_mapping:
                # Еженедельное расписание в локальном времени, соответствующее
                # выбранному московскому HH:MM и дню недели.
                day_mapping[local_day].at(local_hhmm).do(self._schedule_job_wrapper)

                # Если сегодня тот же день и время еще не прошло — отдельная задача на сегодня.
                if current_weekday == schedule_day:
                    schedule_hour, schedule_minute = map(int, schedule_time.split(':'))
                    schedule_datetime = moscow_time.replace(hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0)

                    if moscow_time < schedule_datetime:
                        minutes_until = int((schedule_datetime - moscow_time).total_seconds() / 60)

                        # Та же конвертация для одноразовой задачи — иначе будет дрейф.
                        _, today_local_hhmm = moscow_to_local_fire(schedule_time, schedule_day)
                        schedule.every().day.at(today_local_hhmm).do(self._schedule_job_wrapper).tag('today')

                        logging.info(
                            f"Запуск СЕГОДНЯ в {schedule_time} МСК "
                            f"(через {minutes_until} мин, локально {today_local_hhmm}) "
                            f"+ еженедельно {schedule_day} {schedule_time} МСК"
                        )
                    else:
                        logging.info(
                            f"Еженедельный запуск: каждый {schedule_day} в {schedule_time} МСК "
                            f"(локально {local_day} {local_hhmm})"
                        )
                else:
                    logging.info(
                        f"Еженедельный запуск: каждый {schedule_day} в {schedule_time} МСК "
                        f"(локально {local_day} {local_hhmm})"
                    )

                logging.info(f"Текущее время МСК: {moscow_time.strftime('%Y-%m-%d %H:%M:%S')}")

                if schedule.jobs:
                    next_run = schedule.next_run()
                    logging.info(f"Следующий запуск: {next_run}")
            else:
                logging.error(f"Неизвестный день недели: {schedule_day}")

        except Exception as e:
            logging.error(f"Ошибка обновления расписания: {e}", exc_info=True)
    
    def _scheduler_loop(self):
        """Основной цикл планировщика"""
        logging.info("Поток планировщика запущен")
        
        while self.is_running:
            try:
                schedule.run_pending()
                time.sleep(60)  # Проверяем каждую минуту
            except Exception as e:
                logging.error(f"Ошибка в цикле планировщика: {e}")
                time.sleep(60)
        
        logging.info("Поток планировщика остановлен")
    
    async def start_scheduler(self):
        """Запуск планировщика"""
        if self.is_running:
            logging.warning("Планировщик уже запущен")
            return
        
        try:
            # Принудительно сбрасываем блокировку парсера при запуске планировщика
            self.parser_lock.set_stopped()
            logging.info("Блокировка парсера сброшена при запуске планировщика")
            
            # Сохраняем ссылку на текущий event loop
            self.main_loop = asyncio.get_event_loop()
            
            # Обновляем расписание из базы данных
            await self.update_schedule()
            
            # Запускаем планировщик в отдельном потоке
            self.is_running = True
            self.scheduler_thread = Thread(target=self._scheduler_loop, daemon=True)
            self.scheduler_thread.start()
            
            moscow_time = datetime.now(self.moscow_tz)
            logging.info(f"Планировщик запущен в {moscow_time.strftime('%Y-%m-%d %H:%M:%S')} МСК")
            
            # Уведомляем администраторов о запуске
            try:
                admins = await self.db.get_all_admins()
                schedule_time, schedule_day = await self.db.get_scheduler_settings()
                
                day_names = {
                    "monday": "Понедельник",
                    "tuesday": "Вторник",
                    "wednesday": "Среда",
                    "thursday": "Четверг",
                    "friday": "Пятница",
                    "saturday": "Суббота",
                    "sunday": "Воскресенье"
                }
                
                startup_message = f"""🤖 <b>Бот запущен</b>

📅 <b>Расписание:</b>
• День: {day_names.get(schedule_day, schedule_day)}
• Время: {schedule_time}

✅ Мониторинг активен"""
                
                for admin_id, _ in admins:
                    if self.main_loop and not self.main_loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            self.notification_service.send_message_to_user(admin_id, startup_message),
                            self.main_loop
                        )
                    else:
                        await self.notification_service.send_message_to_user(admin_id, startup_message)
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления о запуске: {e}")
            
        except Exception as e:
            logging.error(f"Ошибка запуска планировщика: {e}")
            self.is_running = False
    
    async def stop_scheduler(self):
        """Остановка планировщика"""
        if not self.is_running:
            logging.warning("Планировщик уже остановлен")
            return
        
        try:
            self.is_running = False
            
            # Ждем завершения потока
            if self.scheduler_thread and self.scheduler_thread.is_alive():
                self.scheduler_thread.join(timeout=5)
            
            # Очищаем расписание
            schedule.clear()
            
            logging.info("Планировщик остановлен")
            
            # Уведомляем администраторов об остановке
            try:
                admins = await self.db.get_all_admins()
                moscow_time = datetime.now(self.moscow_tz)
                
                shutdown_message = f"""🛑 <b>Бот остановлен</b>

❌ Мониторинг неактивен"""
                
                for admin_id, _ in admins:
                    if self.main_loop and not self.main_loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            self.notification_service.send_message_to_user(admin_id, shutdown_message),
                            self.main_loop
                        )
                    else:
                        await self.notification_service.send_message_to_user(admin_id, shutdown_message)
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления об остановке: {e}")
                
        except Exception as e:
            logging.error(f"Ошибка остановки планировщика: {e}")
    
    async def restart_scheduler(self):
        """Перезапуск планировщика с новыми настройками"""
        logging.info("Перезапуск планировщика...")
        await self.stop_scheduler()
        await asyncio.sleep(2)  # Небольшая пауза
        await self.start_scheduler()
    
    def is_scheduler_running(self) -> bool:
        """Проверка, запущен ли планировщик"""
        return self.is_running and (
            self.scheduler_thread is None or 
            self.scheduler_thread.is_alive()
        )