"""
База данных для Telegram бота
"""
import aiosqlite
import logging
from typing import List, Optional, Tuple
from datetime import datetime

class BotDatabase:
    """Класс для работы с базой данных бота"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def init_database(self):
        """Инициализация базы данных"""
        async with aiosqlite.connect(self.db_path) as db:
            # Таблица администраторов (дополнительных, главный админ в .env)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    description TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    added_by INTEGER
                )
            """)
            
            # Таблица настроек планировщика
            await db.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_time TEXT NOT NULL,
                    schedule_day TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by INTEGER
                )
            """)
            
            # Таблица настроек планировщика аналитики
            await db.execute("""
                CREATE TABLE IF NOT EXISTS analytics_scheduler_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_time TEXT NOT NULL,
                    schedule_day TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by INTEGER
                )
            """)
            
            # Миграция: добавляем колонку description если её нет
            try:
                await db.execute("ALTER TABLE admins ADD COLUMN description TEXT")
                logging.info("Добавлена колонка description в таблицу admins")
            except Exception:
                # Колонка уже существует или другая ошибка
                pass
            
            await db.commit()
            logging.info("База данных инициализирована")
    
    async def add_admin(self, user_id: int, username: str = None, description: str = None, added_by: int = None) -> bool:
        """Добавление администратора"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO admins (user_id, username, description, added_by) VALUES (?, ?, ?, ?)",
                    (user_id, username, description, added_by)
                )
                await db.commit()
                logging.info(f"Администратор {user_id} добавлен")
                return True
        except Exception as e:
            logging.error(f"Ошибка добавления администратора: {e}")
            return False
    
    async def remove_admin(self, user_id: int) -> bool:
        """Удаление администратора"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
                await db.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logging.error(f"Ошибка удаления администратора: {e}")
            return False
    
    async def is_admin(self, user_id: int) -> bool:
        """Проверка, является ли пользователь администратором"""
        try:
            # Импортируем config здесь чтобы избежать циклических импортов
            from bot.config import config
            
            # Проверяем главного администратора из .env
            if user_id == config.ADMIN_ID:
                return True
            
            # Проверяем дополнительных администраторов из БД
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
                result = await cursor.fetchone()
                return result is not None
        except Exception as e:
            logging.error(f"Ошибка проверки администратора: {e}")
            return False
    
    async def get_all_admins(self) -> List[Tuple[int, str]]:
        """Получение всех администраторов (включая главного из .env)"""
        try:
            from bot.config import config
            
            # Начинаем с главного администратора
            admins = [(config.ADMIN_ID, "Главный админ")]
            
            # Добавляем дополнительных администраторов из БД
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT user_id, username FROM admins")
                db_admins = await cursor.fetchall()
                admins.extend(db_admins)
            
            return admins
        except Exception as e:
            logging.error(f"Ошибка получения администраторов: {e}")
            return []

    async def get_db_admins_with_description(self) -> List[Tuple[int, str, str]]:
        """Получение дополнительных администраторов с описанием"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Проверяем существование колонки description
                cursor = await db.execute("PRAGMA table_info(admins)")
                columns = await cursor.fetchall()
                has_description = any(col[1] == 'description' for col in columns)
                
                if has_description:
                    cursor = await db.execute("SELECT user_id, username, description FROM admins")
                else:
                    # Если колонки нет, возвращаем с NULL для description
                    cursor = await db.execute("SELECT user_id, username, NULL FROM admins")
                
                return await cursor.fetchall()
        except Exception as e:
            logging.error(f"Ошибка получения администраторов с описанием: {e}")
            return []
    
    async def update_scheduler_settings(self, schedule_time: str, schedule_day: str, updated_by: int) -> bool:
        """Обновление настроек планировщика.

        ``schedule_day`` может содержать несколько дней через запятую:
        ``"monday,thursday"`` — это два запуска в неделю. Старые значения
        вида ``"monday"`` продолжают работать без изменений.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Деактивируем старые настройки
                await db.execute("UPDATE scheduler_settings SET is_active = 0")
                
                # Добавляем новые настройки
                await db.execute(
                    "INSERT INTO scheduler_settings (schedule_time, schedule_day, updated_by) VALUES (?, ?, ?)",
                    (schedule_time, schedule_day, updated_by)
                )
                await db.commit()
                logging.info(f"Настройки планировщика обновлены: {schedule_day} в {schedule_time}")
                return True
        except Exception as e:
            logging.error(f"Ошибка обновления настроек планировщика: {e}")
            return False
    
    async def get_scheduler_settings(self) -> Optional[Tuple[str, str]]:
        """Получение текущих настроек планировщика"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT schedule_time, schedule_day FROM scheduler_settings WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
                )
                result = await cursor.fetchone()
                return result if result else ("10:00", "monday")
        except Exception as e:
            logging.error(f"Ошибка получения настроек планировщика: {e}")
            return ("10:00", "monday")
    
    async def update_analytics_scheduler_settings(self, schedule_time: str, schedule_day: str, updated_by: int) -> bool:
        """Обновление настроек планировщика аналитики.

        Как и у парсинга, ``schedule_day`` может содержать несколько дней
        через запятую: ``"tuesday,friday"``.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE analytics_scheduler_settings SET is_active = 0")
                await db.execute(
                    "INSERT INTO analytics_scheduler_settings (schedule_time, schedule_day, updated_by) VALUES (?, ?, ?)",
                    (schedule_time, schedule_day, updated_by)
                )
                await db.commit()
                logging.info(f"Настройки планировщика аналитики обновлены: {schedule_day} в {schedule_time}")
                return True
        except Exception as e:
            logging.error(f"Ошибка обновления настроек планировщика аналитики: {e}")
            return False
    
    async def get_analytics_scheduler_settings(self) -> Optional[Tuple[str, str]]:
        """Получение текущих настроек планировщика аналитики"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT schedule_time, schedule_day FROM analytics_scheduler_settings WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
                )
                result = await cursor.fetchone()
                return result if result else ("12:00", "monday")
        except Exception as e:
            logging.error(f"Ошибка получения настроек планировщика аналитики: {e}")
            return ("12:00", "monday")
    
