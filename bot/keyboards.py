"""
Клавиатуры для Telegram бота
"""
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_main_menu() -> ReplyKeyboardMarkup:
    """Главное меню бота"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Начать парсинг")],
            [KeyboardButton(text="📈 Аналитика")],
            [KeyboardButton(text="👥 Управление админами"),KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="ℹ️ Помощь"), KeyboardButton(text="⚙️ Настройки")]
        ],
        resize_keyboard=True,
        persistent=True
    )
    return keyboard

def get_settings_menu() -> ReplyKeyboardMarkup:
    """Меню настроек"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🕐 Настроить расписание")],
            [KeyboardButton(text="📋 Текущие настройки")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_admin_menu() -> ReplyKeyboardMarkup:
    """Меню управления администраторами"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить админа")],
            [KeyboardButton(text="Удалить админа")],
            [KeyboardButton(text="📋 Список админов")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    return keyboard

DAY_NAMES = [
    ("Понедельник", "monday"),
    ("Вторник", "tuesday"),
    ("Среда", "wednesday"),
    ("Четверг", "thursday"),
    ("Пятница", "friday"),
    ("Суббота", "saturday"),
    ("Воскресенье", "sunday"),
]

# Пары заданы как (человеческое имя, код) — мапаем именно код → имя.
# dict(DAY_NAMES) перевернул бы ключи и искал бы 'Понедельник' вместо 'monday'.
DAY_NAMES_BY_CODE = {code: name for name, code in DAY_NAMES}


def format_days(schedule_day: str) -> str:
    """'monday,thursday' → 'Понедельник, Четверг'. Старое 'monday' работает."""
    codes = [d.strip() for d in str(schedule_day).split(',') if d.strip()]
    return ", ".join(DAY_NAMES_BY_CODE.get(code, code) for code in codes)


def get_days_keyboard(selected_days=None) -> InlineKeyboardMarkup:
    """Клавиатура выбора дней недели.

    Выбрать можно несколько дней: нажатие переключает день (✓ отмечает
    выбранный), а «Готово» завершает выбор. Раньше нажатие сразу уводило на
    выбор времени, поэтому неделя могла быть только однодневной.
    """
    selected = set(selected_days or [])

    rows = []
    for day_name, day_code in DAY_NAMES:
        mark = "✅ " if day_code in selected else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{mark}{day_name}",
                callback_data=f"day_{day_code}"
            )
        ])

    if selected:
        rows.append([
            InlineKeyboardButton(text="✅ Готово", callback_data="days_done")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_time_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора времени"""
    times = [
        "06:00", "07:00", "08:00", "09:00", "10:00", "11:00",
        "12:00", "13:00", "14:00", "15:00", "16:00", "17:00",
        "18:00", "19:00", "20:00", "21:00", "22:00"
    ]
    
    # Разбиваем на строки по 3 кнопки
    keyboard_rows = []
    for i in range(0, len(times), 3):
        row = [
            InlineKeyboardButton(text=time, callback_data=f"time_{time}")
            for time in times[i:i+3]
        ]
        keyboard_rows.append(row)
    
    # Добавляем кнопку для ввода времени вручную
    keyboard_rows.append([
        InlineKeyboardButton(text="✏️ Ввести время вручную", callback_data="custom_time")
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    return keyboard

def get_back_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой назад"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]],
        resize_keyboard=True
    )
    return keyboard

def get_description_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора добавления описания"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Пропустить описание", callback_data="skip_description")]
        ]
    )
    return keyboard

def get_admin_list_keyboard(admins) -> InlineKeyboardMarkup:
    """Клавиатура со списком админов для удаления"""
    buttons = []
    for user_id, username, description in admins:
        # Показываем описание если есть, иначе ID
        display_text = description if description else f"ID: {user_id}"
        buttons.append([InlineKeyboardButton(
            text=display_text, 
            callback_data=f"delete_admin_{user_id}"
        )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

def get_confirm_delete_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления админа"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{user_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete")]
        ]
    )
    return keyboard

def get_stop_parsing_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для остановки парсинга"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛑 Завершить парсинг", callback_data="stop_parsing")]
        ]
    )
    return keyboard

def get_analytics_menu() -> ReplyKeyboardMarkup:
    """Меню аналитики"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Запустить аналитику")],
            [KeyboardButton(text="⚙️ Настройки аналитики")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_analytics_settings_menu() -> ReplyKeyboardMarkup:
    """Меню настроек аналитики"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🕐 Настроить расписание аналитики")],
            [KeyboardButton(text="📋 Текущие настройки аналитики")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )
    return keyboard