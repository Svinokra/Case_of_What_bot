import asyncio
from datetime import datetime, timedelta
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
import menu_profile
import random
import game_mansion

dice_waiting = {}

scanner_waiting = {}

def ensure_cooldowns_table():
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_cooldowns (
                user_id BIGINT,
                game_name TEXT,
                last_played TIMESTAMP,
                PRIMARY KEY (user_id, game_name)
            )
        """)
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def get_dice_cooldown(user_id: int) -> bool:
    ensure_cooldowns_table()
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT last_played FROM game_cooldowns WHERE user_id = %s AND game_name = 'dice'",
            (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return False
        (last_played,) = row
        if not last_played:
            return False
        return datetime.now() < last_played + timedelta(hours=1)
    finally:
        cursor.close()
        conn.close()


def set_dice_cooldown(user_id: int):
    ensure_cooldowns_table()
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        now = datetime.now()
        cursor.execute("""
            INSERT INTO game_cooldowns (user_id, game_name, last_played)
            VALUES (%s, 'dice', %s)
            ON CONFLICT (user_id, game_name) DO UPDATE SET last_played = EXCLUDED.last_played
        """, (user_id, now))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


async def open_games(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])
    dice_waiting.pop(owner_id, None)
    scanner_waiting.pop(owner_id, None)

    builder = InlineKeyboardBuilder()
    builder.button(text="Кости", callback_data=f"game_dice:{owner_id}")
    builder.button(text="Шашки", callback_data=f"game_checkers:{owner_id}")
    builder.button(text="Сканер", callback_data=f"game_scanner:{owner_id}")
    builder.button(text="Особняк", callback_data=f"game_mansion:{owner_id}")
    builder.button(text="Кейсы", callback_data=f"game_cases:{owner_id}")
    builder.button(text="Сапёр", callback_data=f"game_minesweeper:{owner_id}")
    builder.button(text="Назад", callback_data=f"back_to_menu:{owner_id}")

    builder.adjust(3, 2, 1, 1)

    text = "Раздел: Развлечения\n\nВыберите игру:"
    await callback.message.edit_text(text=text, reply_markup=builder.as_markup())
    
async def start_dice_game(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])

    if get_dice_cooldown(user.id):
        builder = InlineKeyboardBuilder()
        builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
        builder.adjust(1)

        await callback.message.edit_text(
            text="Вы можете играть в кости только через 1 час",
            reply_markup=builder.as_markup()
        )
        return

    try:
        await callback.message.delete()
    except Exception:
        pass

    mention = f"@{user.username}" if user.username else user.first_name
    builder = InlineKeyboardBuilder()
    builder.button(text="Назад", callback_data=f"dice_cancel:{owner_id}")
    builder.adjust(1)

    prompt_msg = await callback.message.answer(
        text=f"{mention} Ответьте на это сообщение командой /moyastavka {{ваша цифра от 1 до 6}}",
        reply_markup=builder.as_markup()
    )

    dice_waiting[user.id] = {
        "chat_id": callback.message.chat.id,
        "thread_id": getattr(callback.message, "message_thread_id", None),
        "prompt_msg_id": prompt_msg.message_id
    }

async def cancel_dice_game(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])
    dice_waiting.pop(owner_id, None)
    await open_games(callback)


async def process_dice_bet(message: types.Message) -> bool:
    user = message.from_user
    if not user or user.id not in dice_waiting:
        return False

    state = dice_waiting[user.id]

    if not message.reply_to_message or message.reply_to_message.message_id != state["prompt_msg_id"]:
        return False

    if not message.text:
        return False

    parts = message.text.strip().split()
    if len(parts) != 2:
        return False
    cmd, bet_str = parts
    if not cmd.startswith("/moyastavka") or bet_str not in ["1", "2", "3", "4", "5", "6"]:
        return False

    bet = int(bet_str)

    dice_waiting.pop(user.id, None)
    set_dice_cooldown(user.id)

    try:
        await message.bot.edit_message_reply_markup(
            chat_id=state["chat_id"],
            message_id=state["prompt_msg_id"],
            reply_markup=None
        )
    except Exception:
        pass

    dice_msg = await message.reply_dice(emoji="🎲")
    rolled_value = dice_msg.dice.value

    await asyncio.sleep(5.0)

    menu_profile.get_or_create_profile(user.id, user.username, user.full_name)
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT coins FROM user_profiles WHERE user_id = %s", (user.id,))
        row = cursor.fetchone()
        (current_coins,) = row if row else (0,)

        if rolled_value == bet:
            new_coins = current_coins + 8
            cursor.execute("UPDATE user_profiles SET coins = %s WHERE user_id = %s", (new_coins, user.id))
            conn.commit()
            outcome_line = "Итог: Выигрыш, на ваш баланс начислено 8 СЧ койнов"
        else:
            if current_coins > 0:
                new_coins = max(0, current_coins - 8)
                cursor.execute("UPDATE user_profiles SET coins = %s WHERE user_id = %s", (new_coins, user.id))
                conn.commit()
                outcome_line = "Итог: Проигрыш, отнимаем у вас 8 СЧ койнов"
            else:
                outcome_line = "Итог: Проигрыш"
    finally:
        cursor.close()
        conn.close()

    result_text = (
        f"Выпало: {rolled_value}\n"
        f"Ваша ставка: {bet}\n\n"
        f"{outcome_line}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"menu_games:{user.id}")
    builder.adjust(1)

    await dice_msg.reply(text=result_text, reply_markup=builder.as_markup())
    return True

async def start_scanner_game(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])

    try:
        await callback.message.delete()
    except Exception:
        pass

    mention = f"@{user.username}" if user.username else user.first_name
    builder = InlineKeyboardBuilder()
    builder.button(text="Назад", callback_data=f"scanner_cancel:{owner_id}")
    builder.adjust(1)

    prompt_msg = await callback.message.answer(
        text=f"{mention} Ответьте командой /scan на сообщение любого участника",
        reply_markup=builder.as_markup()
    )

    scanner_waiting[user.id] = {
        "chat_id": callback.message.chat.id,
        "thread_id": getattr(callback.message, "message_thread_id", None),
        "prompt_msg_id": prompt_msg.message_id
    }

# ГЕЙМПЛЕЙ ИГРЫ "СКАНЕР"
async def cancel_scanner_game(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])
    scanner_waiting.pop(owner_id, None)
    await open_games(callback)

def build_scanner_result(target_mention: str, percent: int) -> str:
    scale = "⬜️🟦🟩🟨🟧🟥⬛️"

    if percent <= 14:
        arrow = "🔺"
        status = f"Уровень: Безопасный ({percent}% дозы) 😇"
    elif percent <= 28:
        arrow = "       🔺"
        status = f"Уровень: Низкий ({percent}% дозы) 😊"
    elif percent <= 42:
        arrow = "              🔺"
        status = f"Уровень: Умеренный ({percent}% дозы) 🙂"
    elif percent <= 57:
        arrow = "                    🔺"
        status = f"Уровень: Средний ({percent}% дозы) 😐"
    elif percent <= 71:
        arrow = "                           🔺"
        status = f"Уровень: Повышенный ({percent}% дозы) 😳"
    elif percent <= 85:
        arrow = "                                  🔺"
        status = f"Уровень: Высокий ({percent}% дозы) 😨"
    else:
        arrow = "                                         🔺"
        status = f"Уровень: Критический ({percent}% дозы) 💀"

    return (
        f"Результат сканирования {target_mention}\n"
        f"{scale}\n"
        f"{arrow}\n"
        f"{status}"
    )

async def process_scan_command(message: types.Message) -> bool:
    user = message.from_user
    if not user or user.id not in scanner_waiting:
        return False

    state = scanner_waiting[user.id]

    if message.chat.id != state["chat_id"]:
        return False
    if getattr(message, "message_thread_id", None) != state["thread_id"]:
        return False

    # Проверяем, что это реальный Reply на сообщение участника (а не на системный топик форума)
    reply_msg = message.reply_to_message
    if not reply_msg or getattr(reply_msg, "forum_topic_created", None) is not None:
        return False

    target_user = reply_msg.from_user
    if not target_user or target_user.is_bot:
        return False

    # Убираем игрока из ожидания и чистим кнопку "Назад" у инструкции
    scanner_waiting.pop(user.id, None)
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=state["chat_id"],
            message_id=state["prompt_msg_id"],
            reply_markup=None
        )
    except Exception:
        pass

    target_mention = f"@{target_user.username}" if target_user.username else target_user.first_name

    # Первое сообщение о начале сканирования
    scan_msg = await message.reply(f"Сканирование участника {target_mention}")

    # Ждём 4 секунды
    await asyncio.sleep(4.0)

    percent = random.randint(0, 100)
    result_text = build_scanner_result(target_mention, percent)

    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"menu_games:{user.id}")
    builder.adjust(1)

    await scan_msg.edit_text(text=result_text, reply_markup=builder.as_markup())
    return True

async def handle_games_callbacks(callback: types.CallbackQuery):
    data = callback.data

    if data.startswith("game_dice:"):
        await start_dice_game(callback)
    elif data.startswith("dice_cancel:"):
        await cancel_dice_game(callback)
    elif data.startswith("game_scanner:"):
        await start_scanner_game(callback)
    elif data.startswith("scanner_cancel:"):
        await cancel_scanner_game(callback)
    elif data.startswith("game_mansion:"):
        await game_mansion.open_mansion_intro(callback)
    elif data.startswith(("game_checkers:", "game_cases:", "game_minesweeper:")):
        await callback.answer("Эта игра пока находится в разработке", show_alert=True)
