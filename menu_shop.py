from datetime import datetime, timedelta
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
import menu_profile

# Справочник всех товаров магазина (удобно менять цены, описания и время действия)
SHOP_ITEMS = {
    # Категория "Полезное"
    "luck": {
        "category": "useful",
        "name": "🍀 Удача",
        "description": "Повышает шанс выигрыша в костях, а в особняке повышает шанс на комнаты с выбором",
        "duration_text": "5 минут",
        "price": 20,
    },
    "shield": {
        "category": "useful",
        "name": "🛡️ Защита",
        "description": "Попав в опасную комнату особняка то щит сразу активируется и вы спасёте свою жизнь, вы можете купить только 6 штук",
        "duration_text": "Однократное авто использование",
        "price": 35,
    },
    "doubler": {
        "category": "useful",
        "name": "⚡ Удвоитель",
        "description": "Удваивает награду в играх",
        "duration_text": "1 час",
        "price": 42,
    },
    # Категория "Рофлы" (названия, описания, время и цены можно менять здесь)
    "rofl_item": {
        "category": "rofl",
        "name": "Резиновый",
        "description": "Для дарка самое то, уверен что хочешь накопить на это?",
        "duration_text": "Бесконечно",
        "price": 9999,
    },
    "rofl_costume": {
        "category": "rofl",
        "name": "Аутфит Сильвестра",
        "description": "Набор тёмного костюма с зелёной галстук бабочкой, тёмными брюками, жёсткими тягами, шляпой цилиндром и одной тыквой, уверен что хочешь накопить на это?",
        "duration_text": "Бесконечно",
        "price": 7575,
    },
}


def ensure_buffs_table():
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_buffs (
                user_id BIGINT PRIMARY KEY,
                luck_until TIMESTAMP NULL,
                double_until TIMESTAMP NULL,
                shields_count INT DEFAULT 0
            )
        """)
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def get_user_buffs(user_id: int) -> dict:
    """Возвращает актуальные баффы игрока: {'luck': bool, 'doubler': bool, 'shields': int}."""
    ensure_buffs_table()
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT luck_until, double_until, shields_count FROM user_buffs WHERE user_id = %s",
            (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {"luck": False, "doubler": False, "shields": 0}

        luck_until, double_until, shields_count = row
        now = datetime.now()
        return {
            "luck": bool(luck_until and now < luck_until),
            "doubler": bool(double_until and now < double_until),
            "shields": shields_count or 0,
        }
    finally:
        cursor.close()
        conn.close()


# 1. ГЛАВНЫЙ ЭКРАН МАГАЗИНА (ВЫБОР КАТЕГОРИИ)
async def open_shop(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])

    builder = InlineKeyboardBuilder()
    builder.button(text="Полезное", callback_data=f"shop_cat:useful:{owner_id}")
    builder.button(text="Рофлы", callback_data=f"shop_cat:rofl:{owner_id}")
    builder.button(text="Назад", callback_data=f"back_to_menu:{owner_id}")
    builder.adjust(2, 1)

    text = "Раздел: Магазин\n\nВыберите категорию:"
    await callback.message.edit_text(text=text, reply_markup=builder.as_markup())


# 2. ЭКРАН КАТЕГОРИИ ТОВАРОВ
async def open_shop_category(callback: types.CallbackQuery, category: str, owner_id: int):
    builder = InlineKeyboardBuilder()

    if category == "useful":
        builder.button(text="🍀 Удача", callback_data=f"shop_item:luck:{owner_id}")
        builder.button(text="🛡️ Защита", callback_data=f"shop_item:shield:{owner_id}")
        builder.button(text="⚡ Удвоитель", callback_data=f"shop_item:doubler:{owner_id}")
        builder.button(text="Назад", callback_data=f"menu_shop:{owner_id}")
        builder.adjust(2, 1, 1)
        text = "Категория: Полезное\n\nВыберите товар:"
    else:
        builder.button(text=SHOP_ITEMS["rofl_item"]["name"], callback_data=f"shop_item:rofl_item:{owner_id}")
        builder.button(text=SHOP_ITEMS["rofl_costume"]["name"], callback_data=f"shop_item:rofl_costume:{owner_id}")
        builder.button(text="Назад", callback_data=f"menu_shop:{owner_id}")
        builder.adjust(1, 1, 1)
        text = "Категория: Рофлы\n\nВыберите товар:"

    await callback.message.edit_text(text=text, reply_markup=builder.as_markup())

# 3. КАРТОЧКА КОНКРЕТНОГО ТОВАРА
async def open_shop_item(callback: types.CallbackQuery, item_key: str, owner_id: int):
    item = SHOP_ITEMS.get(item_key)
    if not item:
        await open_shop(callback)
        return

    extra_info = ""
    if item_key == "shield":
        buffs = get_user_buffs(owner_id)
        extra_info = f"\nУ вас сейчас щитов: {buffs['shields']}/6"

    text = (
        f"Выбранный товар: {item['name']}\n\n"
        f"Описание: {item['description']}\n\n"
        f"Время действия: {item['duration_text']}{extra_info}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text=f"🪙 {item['price']}", callback_data=f"shop_buy:{item_key}:{owner_id}")
    builder.button(text="Назад", callback_data=f"shop_cat:{item['category']}:{owner_id}")
    builder.adjust(1, 1)

    await callback.message.edit_text(text=text, reply_markup=builder.as_markup())


# 4. ПОКУПКА И АКТИВАЦИЯ ТОВАРА
async def buy_shop_item(callback: types.CallbackQuery, item_key: str, owner_id: int):
    user = callback.from_user
    item = SHOP_ITEMS.get(item_key)
    if not item:
        return

    price = item["price"]
    ensure_buffs_table()
    menu_profile.get_or_create_profile(user.id, user.username, user.full_name)

    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. Проверяем баланс игрока
        cursor.execute("SELECT coins FROM user_profiles WHERE user_id = %s", (user.id,))
        row = cursor.fetchone()
        (current_coins,) = row if row else (0,)

        if current_coins < price:
            await callback.answer(
                f"Недостаточно СЧ койнов. Нужно {price}, у вас {current_coins}",
                show_alert=True
            )
            return

        # 2. Проверка лимитов и уже активных бустов
        now = datetime.now()
        if item_key == "shield":
            cursor.execute("SELECT shields_count FROM user_buffs WHERE user_id = %s", (user.id,))
            s_row = cursor.fetchone()
            (current_shields,) = s_row if s_row else (0,)
            if (current_shields or 0) >= 6:
                await callback.answer("Вы уже купили максимум щитов (6/6)", show_alert=True)
                return

        elif item_key == "luck":
            cursor.execute("SELECT luck_until FROM user_buffs WHERE user_id = %s", (user.id,))
            l_row = cursor.fetchone()
            (luck_until,) = l_row if l_row else (None,)
            if luck_until and now < luck_until:
                await callback.answer("Буст 🍀 Удача у вас уже активен", show_alert=True)
                return

        elif item_key == "doubler":
            cursor.execute("SELECT double_until FROM user_buffs WHERE user_id = %s", (user.id,))
            d_row = cursor.fetchone()
            (double_until,) = d_row if d_row else (None,)
            if double_until and now < double_until:
                await callback.answer("Буст ⚡ Удвоитель у вас уже активен", show_alert=True)
                return

        # 3. Списываем СЧ койны
        new_coins = current_coins - price
        cursor.execute("UPDATE user_profiles SET coins = %s WHERE user_id = %s", (new_coins, user.id))

        # 4. Применяем эффект товара
        now = datetime.now()
        if item_key == "luck":
            luck_until = now + timedelta(minutes=5)
            cursor.execute("""
                INSERT INTO user_buffs (user_id, luck_until)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET luck_until = EXCLUDED.luck_until
            """, (user.id, luck_until))
            result_msg = "Вы успешно купили 🍀 Удачу!\nБуст активен в течение 5 минут"

        elif item_key == "shield":
            cursor.execute("""
                INSERT INTO user_buffs (user_id, shields_count)
                VALUES (%s, 1)
                ON CONFLICT (user_id) DO UPDATE SET shields_count = COALESCE(user_buffs.shields_count, 0) + 1
            """, (user.id,))
            result_msg = "Вы успешно купили 🛡️ Защиту!\nЩит автоматически спасёт вас при попадании в опасную комнату особняка"

        elif item_key == "doubler":
            double_until = now + timedelta(hours=1)
            cursor.execute("""
                INSERT INTO user_buffs (user_id, double_until)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET double_until = EXCLUDED.double_until
            """, (user.id, double_until))
            result_msg = "Вы успешно купили ⚡ Удвоитель!\nНаграды в играх будут удваиваться в течение 1 часа"

        else:
            # Шуточные товары ("Рофлы")
            result_msg = f"Вы успешно приобрели «{item['name']}»! Поздравляем с покупкой"

        conn.commit()
    finally:
        cursor.close()
        conn.close()

    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"shop_cat:{item['category']}:{owner_id}")
    builder.adjust(1)

    await callback.message.edit_text(text=result_msg, reply_markup=builder.as_markup())


# 5. РОУТЕР КОЛБЭКОВ МАГАЗИНА
async def handle_shop_callbacks(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    action = parts[0]
    owner_id = int(parts[-1])

    if action == "shop_cat":
        category = parts[1]
        await open_shop_category(callback, category, owner_id)
    elif action == "shop_item":
        item_key = parts[1]
        await open_shop_item(callback, item_key, owner_id)
    elif action == "shop_buy":
        item_key = parts[1]
        await buy_shop_item(callback, item_key, owner_id)
