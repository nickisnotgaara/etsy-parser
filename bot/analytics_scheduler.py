"""
Планировщик аналитики с интеграцией Telegram бота
"""
import asyncio
import logging
import schedule
import time
import pytz
from datetime import datetime
from threading import Thread
from typing import Optional

from bot.database import BotDatabase
from bot.notifications import NotificationService
from services.analytics_service import AnalyticsService


class AnalyticsScheduler:
    """Планировщик аналитики с интеграцией Telegram бота"""
    
    def __init__(self, notification_service: NotificationService, db: BotDatabase):
        self.notification_service = notification_service
        self.db = db
        self.analytics_service = AnalyticsService()
        self.is_running = False
        self.scheduler_thread: Optional[Thread] = None
        self.moscow_tz = pytz.timezone('Europe/Moscow')
        self.main_loop = None
    
    async def scheduled_analytics_job(self, user_id: int = None):
        """Задача аналитики с уведомлениями"""
        try:
            logging.info("Запуск аналитики с уведомлениями")
            
            if user_id:
                await self.notification_service.send_message_to_user(
                    user_id,
                    "🚀 Запуск процесса аналитики...\n\n⏳ Получение данных от EverBee..."
                )
            
            listing_ids = self.analytics_service.get_all_listing_ids()
            
            if not listing_ids:
                msg = "⚠️ Нет листингов для аналитики.\n\nСначала запустите парсинг для поиска новых товаров."
                if user_id:
                    await self.notification_service.send_message_to_user(user_id, msg)
                else:
                    admins = await self.db.get_all_admins()
                    for admin_id, _ in admins:
                        await self.notification_service.send_message_to_user(admin_id, msg)
                return
            
            if user_id:
                await self.notification_service.send_message_to_user(
                    user_id,
                    f"📊 Найдено {len(listing_ids)} листингов\n🔄 Получение актуальной статистики..."
                )
            
            timestamp, current_stats = self.analytics_service.run_analytics()
            
            if not current_stats:
                msg = "❌ Не удалось получить данные от EverBee.\n\nПроверьте токен и попробуйте снова."
                if user_id:
                    await self.notification_service.send_message_to_user(user_id, msg)
                else:
                    admins = await self.db.get_all_admins()
                    for admin_id, _ in admins:
                        await self.notification_service.send_message_to_user(admin_id, msg)
                return
            
            # Проверяем топы
            from services.tops_service import TopsService
            tops_service = TopsService()
            data = self.analytics_service._load_listings_data()
            potential_tops = tops_service._check_listings_age(data, timestamp)
            
            # Отправляем уведомления о топах
            if potential_tops:
                top_listings_data = tops_service._load_top_listings()
                admins = await self.db.get_all_admins()
                
                for listing_id in potential_tops:
                    if listing_id in top_listings_data.get("listings", {}):
                        summary = top_listings_data["listings"][listing_id]
                        top_message = tops_service.format_top_hit_message(summary)
                        
                        for admin_id, _ in admins:
                            try:
                                await self.notification_service.send_message_to_user(admin_id, top_message)
                            except Exception as e:
                                logging.error(f"Ошибка отправки уведомления о топе {admin_id}: {e}")
            
            tops_msg = f"\n🔥 Найдено {len(potential_tops)} топ-хитов!" if potential_tops else ""
            
            if user_id:
                await self.notification_service.send_message_to_user(
                    user_id,
                    f"✅ Данные получены и сохранены!\n\n"
                    f"📅 Временная метка: {timestamp}\n"
                    f"📦 Обновлено листингов: {len(current_stats)}{tops_msg}\n\n"
                    f"🔄 Формирование отчета об изменениях..."
                )
            
            report = self.analytics_service.generate_changes_report()
            
            # Удаляем промежуточные снимки после сравнения
            self.analytics_service.cleanup_old_snapshots()
            
            if not report:
                msg = "ℹ️ Это первый снимок статистики или нет изменений."
                if user_id:
                    await self.notification_service.send_message_to_user(user_id, msg)
                return
            
            formatted_message = self.analytics_service.format_changes_message(report)
            
            # Отправляем отчет всем админам
            admins = await self.db.get_all_admins()
            for admin_id, _ in admins:
                try:
                    await self.notification_service.send_long_message(admin_id, formatted_message, "HTML")
                except Exception as e:
                    logging.error(f"Ошибка отправки отчета админу {admin_id}: {e}")
            
            logging.info("Аналитика завершена успешно")
            
        except Exception as e:
            logging.error(f"Ошибка в аналитике: {e}")
            error_message = f"❌ <b>Ошибка аналитики</b>\n\n🚨 Ошибка: {str(e)[:200]}"
            
            try:
                if user_id:
                    await self.notification_service.send_message_to_user(user_id, error_message)
                else:
                    admins = await self.db.get_all_admins()
                    for admin_id, _ in admins:
                        await self.notification_service.send_message_to_user(admin_id, error_message)
            except Exception:
                pass
    
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
                future = asyncio.run_coroutine_threadsafe(
                    self.scheduled_analytics_job(), self.main_loop
                )
                future.result()
            else:
                logging.error(
                    "[АНАЛИТИКА] Планировщик не может запустить задачу: "
                    "основной event loop бота недоступен или закрыт. "
                    "Пропускаю запуск до следующего цикла."
                )
        except Exception as e:
            logging.error(f"Ошибка в обертке планировщика аналитики: {e}")
    
    async def update_schedule(self):
        """Обновление расписания с умным расчетом времени"""
        try:
            schedule.clear('analytics')
            schedule.clear('analytics_today')

            schedule_time, schedule_day = await self.db.get_analytics_scheduler_settings()
            logging.info(f"[АНАЛИТИКА] Настройки из БД: {schedule_day} в {schedule_time}")

            moscow_time = datetime.now(self.moscow_tz)
            current_weekday = moscow_time.strftime('%A').lower()
            logging.info(f"[АНАЛИТИКА] Текущее время МСК: {moscow_time.strftime('%Y-%m-%d %H:%M:%S')} ({current_weekday})")

            # Конвертируем «МСК день + время» → «локальное время системы».
            from utils.moscow_schedule import moscow_to_local_fire

            day_mapping = {
                "monday": schedule.every().monday,
                "tuesday": schedule.every().tuesday,
                "wednesday": schedule.every().wednesday,
                "thursday": schedule.every().thursday,
                "friday": schedule.every().friday,
                "saturday": schedule.every().saturday,
                "sunday": schedule.every().sunday
            }

            # Несколько дней через запятую: "tuesday,friday".
            days = [d.strip().lower() for d in str(schedule_day).split(',') if d.strip()]
            if not days:
                logging.error("[АНАЛИТИКА] Не выбран ни один день недели")
                return

            created = []
            for day in days:
                local_day, local_hhmm = moscow_to_local_fire(schedule_time, day)
                if local_day not in day_mapping:
                    logging.error(f"[АНАЛИТИКА] Неизвестный день недели: {day}")
                    continue
                day_mapping[local_day].at(local_hhmm).do(self._schedule_job_wrapper).tag('analytics')
                created.append(f"{day} {local_hhmm}")

            if not created:
                logging.error(f"[АНАЛИТИКА] Не удалось создать задачи по дням: {days}")
                return

            logging.info(
                f"[АНАЛИТИКА] Еженедельный запуск ({len(created)} дн.): "
                f"{', '.join(days)} в {schedule_time} МСК (локально: {', '.join(created)})"
            )

            # Одноразовая задача на сегодня — не больше одной.
            if current_weekday in days:
                schedule_hour, schedule_minute = map(int, schedule_time.split(':'))
                schedule_datetime = moscow_time.replace(
                    hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0
                )

                if moscow_time < schedule_datetime:
                    minutes_until = int((schedule_datetime - moscow_time).total_seconds() / 60)

                    _, today_local_hhmm = moscow_to_local_fire(schedule_time, current_weekday)
                    schedule.every().day.at(today_local_hhmm).do(self._schedule_job_wrapper).tag('analytics_today')

                    logging.info(
                        f"[АНАЛИТИКА] Запуск СЕГОДНЯ в {schedule_time} МСК "
                        f"(через {minutes_until} мин, локально {today_local_hhmm}) "
                        f"+ еженедельно: {', '.join(days)}"
                    )

            analytics_jobs = [job for job in schedule.jobs if 'analytics' in job.tags]
            logging.info(f"[АНАЛИТИКА] Всего задач аналитики в schedule: {len(analytics_jobs)}")
            for job in analytics_jobs:
                logging.info(f"[АНАЛИТИКА] Задача: {job.tags}, следующий запуск: {job.next_run}")

        except Exception as e:
            logging.error(f"[АНАЛИТИКА] Ошибка обновления расписания: {e}", exc_info=True)
    
    def _scheduler_loop(self):
        """Основной цикл планировщика"""
        logging.info("[АНАЛИТИКА] Поток планировщика запущен")
        
        while self.is_running:
            try:
                schedule.run_pending()
                time.sleep(60)
            except Exception as e:
                logging.error(f"[АНАЛИТИКА] Ошибка в цикле планировщика: {e}")
                time.sleep(60)
        
        logging.info("[АНАЛИТИКА] Поток планировщика остановлен")
    
    async def start_scheduler(self):
        """Запуск планировщика"""
        if self.is_running:
            logging.warning("[АНАЛИТИКА] Планировщик уже запущен")
            return
        
        try:
            logging.info("[АНАЛИТИКА] Начинаем запуск планировщика...")
            self.main_loop = asyncio.get_event_loop()
            await self.update_schedule()
            
            self.is_running = True
            self.scheduler_thread = Thread(target=self._scheduler_loop, daemon=True)
            self.scheduler_thread.start()
            
            moscow_time = datetime.now(self.moscow_tz)
            logging.info(f"[АНАЛИТИКА] Планировщик запущен в {moscow_time.strftime('%Y-%m-%d %H:%M:%S')} МСК")
            logging.info(f"[АНАЛИТИКА] Поток планировщика: {self.scheduler_thread.is_alive()}")
            
            # Уведомляем администраторов о запуске
            try:
                admins = await self.db.get_all_admins()
                schedule_time, schedule_day = await self.db.get_analytics_scheduler_settings()
                
                day_names = {
                    "monday": "Понедельник",
                    "tuesday": "Вторник",
                    "wednesday": "Среда",
                    "thursday": "Четверг",
                    "friday": "Пятница",
                    "saturday": "Суббота",
                    "sunday": "Воскресенье"
                }
                
                startup_message = f"""📈 <b>Планировщик аналитики запущен</b>

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
                logging.error(f"[АНАЛИТИКА] Ошибка отправки уведомления о запуске: {e}")
            
        except Exception as e:
            logging.error(f"[АНАЛИТИКА] Ошибка запуска планировщика: {e}", exc_info=True)
            self.is_running = False
    
    async def stop_scheduler(self):
        """Остановка планировщика"""
        if not self.is_running:
            logging.warning("[АНАЛИТИКА] Планировщик уже остановлен")
            return
        
        try:
            self.is_running = False
            
            if self.scheduler_thread and self.scheduler_thread.is_alive():
                self.scheduler_thread.join(timeout=5)
            
            schedule.clear('analytics')
            schedule.clear('analytics_today')
            
            logging.info("[АНАЛИТИКА] Планировщик остановлен")
            
            # Уведомляем администраторов об остановке
            try:
                admins = await self.db.get_all_admins()
                
                shutdown_message = f"""🛑 <b>Планировщик аналитики остановлен</b>

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
                logging.error(f"[АНАЛИТИКА] Ошибка отправки уведомления об остановке: {e}")
                
        except Exception as e:
            logging.error(f"[АНАЛИТИКА] Ошибка остановки планировщика: {e}")
    
    async def restart_scheduler(self):
        """Перезапуск планировщика с новыми настройками"""
        logging.info("[АНАЛИТИКА] Перезапуск планировщика...")
        await self.stop_scheduler()
        await asyncio.sleep(2)
        await self.start_scheduler()
        logging.info("[АНАЛИТИКА] Перезапуск завершен")
    
    def is_scheduler_running(self) -> bool:
        """Проверка, запущен ли планировщик"""
        return self.is_running and (
            self.scheduler_thread is None or 
            self.scheduler_thread.is_alive()
        )
