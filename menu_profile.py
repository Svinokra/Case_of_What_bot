import os
import logging
import urllib.parse
from datetime import datetime
import pg8000
from aiogram import types, Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ID Создателя бота (только он управляет заметками)
CREATOR_BOT_ID = 7847585206  # Поменяйте на ваш реальный Telegram ID

def get_db_connection():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return pg8000.connect(user="postgres", host="localhost", database="postgres", password="root")
    
    result = urllib.parse.urlparse(db_url)
    return pg8000.connect(
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port or 5432,
        database=result.path[1:]
    )

def get_or_create_profile(user_id: int, username: str, full_name: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                description TEXT DEFAULT 'Отсутствует',
                gender TEXT DEFAULT 'Не выбран',
                coins INT DEFAULT 0,
                reputation INT DEFAULT 100,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note TEXT DEFAULT 'Отсутствует'
            )
        """)
        conn.commit()
        
        cursor.execute("SELECT username, description, gender, coins, reputation, joined_date, note FROM user_profiles WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        
        if row:
            if row[0] != username:
                cursor.execute("UPDATE user_profiles SET username = %s WHERE user_id = %s", (username, user_id))
                conn.commit()
            return row
        else:
            now = datetime.now()
            cursor.execute("""
                INSERT INTO user_profiles (user_id, username, full_name, joined_date)
                VALUES (%s, %s, %s, %s)
            """, (user_id, username, full_name, now))
            conn.commit()
            return (username, "Отсутствует", "Не выбран", 0, 100, now, "Отсутствует")
    finally:
        cursor.close()
        conn.close()

async def get_user_title(chat_id: int, user_id: int, bot: Bot) -> str:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        
        # Проверяем кастомный тег у любого статуса, если Telegram его отдает
        if hasattr(member, 'custom_title') and member.custom_title:
            return member.custom_title
            
        if member.status == "creator":
            return "Владелец"
        elif member.status == "administrator":
            return "Администратор"
            
        return "Участник"
    except Exception:
        return "Участник"

def make_profile_text(username: str, title: str, db_row) -> str:
    username, description, gender, coins, reputation, joined_date, note = db_row
    date_str = joined_date.strftime("%d.%m.%Y в %H:%M")
    mention = f"@{username}" if username and username != "None" else "Отсутствует"
    
    return (
        f"📋Профиль: {mention}\n\n"
        f"✏️Описание:\n{description}\n"
        f"👤 Пол: {gender}\n"
        f"🎖️ Должность: {title}\n"
        f"🪙 СЧ койны: {coins}\n"
        f"✨Репутация: {reputation}\n"
        f"📅 Во вселенной бота: {date_str}\n"
        f"🔖 Заметка от создателя:\n{note}\n\n"
        f"🏆 Место в рейтинге по СЧ койнам: 0\n"
        f"🎗️ Место в рейтинге по репутации: 0"
    )

async def open_profile(callback: types.CallbackQuery, owner_id: int):
    user = callback.from_user
    db_row = get_or_create_profile(user.id, user.username, user.full_name)
    title = await get_user_title(callback.message.chat.id, user.id, callback.bot)
    
    text = make_profile_text(user.username, title, db_row)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Изменить профиль", callback_data=f"edit_profile:{user.id}")
    builder.button(text="Назад", callback_data=f"back_to_menu:{owner_id}")
    builder.adjust(1)
    
    await callback.message.edit_text(text=text, reply_markup=builder.as_markup())

async def show_profile_by_reply(message: types.Message, target_user: types.User, bot: Bot):
    db_row = get_or_create_profile(target_user.id, target_user.username, target_user.full_name)
    title = await get_user_title(message.chat.id, target_user.id, bot)
    text = make_profile_text(target_user.username, title, db_row)
    
    builder = InlineKeyboardBuilder()
    if message.from_user.id == target_user.id:
        builder.button(text="Изменить профиль", callback_data=f"edit_profile:{target_user.id}")
        await message.reply(text=text, reply_markup=builder.as_markup())
    else:
        await message.reply(text=text)

async def show_profile_by_username(message: types.Message, username_arg: str, bot: Bot):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT user_id, username, description, gender, coins, reputation, joined_date, note 
            FROM user_profiles WHERE LOWER(username) = %s
        """, (username_arg.lower(),))
        row = cursor.fetchone()
        
        if not row:
            return
            
        user_id, username, description, gender, coins, reputation, joined_date, note = row
        title = await get_user_title(message.chat.id, user_id, bot)
        
        db_row = (username, description, gender, coins, reputation, joined_date, note)
        text = make_profile_text(username, title, db_row)
        
        builder = InlineKeyboardBuilder()
        if message.from_user.id == user_id:
            builder.button(text="Изменить профиль", callback_data=f"edit_profile:{user_id}")
            await message.reply(text=text, reply_markup=builder.as_markup())
        else:
            await message.reply(text=text)
    finally:
        cursor.close()
        conn.close()

# ЛОГИКА ИЗМЕНЕНИЯ РЕПУТАЦИИ
async def handle_reputation_change(message: types.Message):
    target_user = message.reply_to_message.from_user
    
    # Защита: нельзя менять репутацию самому себе
    if message.from_user.id == target_user.id:
        return

    args = message.text.split()
    if len(args) < 2:
        return
        
    try:
        val = int(args[1])
        if not (1 <= val <= 50):
            return
    except ValueError:
        return

    # Убедимся, что профиль цели существует в базе
    get_or_create_profile(target_user.id, target_user.username, target_user.full_name)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT reputation FROM user_profiles WHERE user_id = %s", (target_user.id,))
        current_rep = cursor.fetchone()[0]
        
        if message.text.startswith("/plus_rep"):
            new_rep = min(100, current_rep + val)
            success_text = "Репутация этого участника была повышена"
        else:
            new_rep = max(0, current_rep - val)
            success_text = "Репутация этого участника была понижена"
            
        cursor.execute("UPDATE user_profiles SET reputation = %s WHERE user_id = %s", (new_rep, target_user.id))
        conn.commit()
        await message.reply(success_text)
    finally:
        cursor.close()
        conn.close()

# ЛОГИКА ЗАМЕТОК (ТОЛЬКО ДЛЯ СОЗДАТЕЛЯ БОТА)
async def handle_note_management(message: types.Message):
    if message.from_user.id != CREATOR_BOT_ID:
        return

    target_user = message.reply_to_message.from_user
    get_or_create_profile(target_user.id, target_user.username, target_user.full_name)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if message.text.startswith("/give_note"):
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                return
            note_text = args[1].strip()
            cursor.execute("UPDATE user_profiles SET note = %s WHERE user_id = %s", (note_text, target_user.id))
            conn.commit()
            await message.reply("Этому участнику была присвоена заметка")
        
        elif message.text.startswith("/delete_note"):
            # Исправлено: добавлен кортеж (target_user.id,) для корректного SQL
            cursor.execute("UPDATE user_profiles SET note = 'Отсутствует' WHERE user_id = %s", (target_user.id,))
            conn.commit()
            await message.reply("Заметка была удалена из профиля этого участника")
    finally:
        cursor.close()
        conn.close()

# 1. ПЕРЕХОД В РАЗДЕЛ ИЗМЕНЕНИЯ ПРОФИЛЯ
async def open_edit_menu(callback: types.CallbackQuery, target_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="Описание", callback_data=f"edit_desc:{target_id}")
    builder.button(text="Пол", callback_data=f"edit_gender:{target_id}")
    builder.button(text="Назад", callback_data=f"back_to_profile:{target_id}")
    builder.adjust(2, 1)

    text = "Раздел: Изменение профиля\n\nЧто вы хотите изменить в своём профиле?"
    await callback.message.edit_text(text=text, reply_markup=builder.as_markup())

async def open_gender_menu(callback: types.CallbackQuery, target_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="Мужской", callback_data=f"set_gender:male:{target_id}")
    builder.button(text="Женский", callback_data=f"set_gender:female:{target_id}")
    builder.button(text="Отмена", callback_data=f"open_edit_menu:{target_id}")
    builder.adjust(2, 1)

    text = "Раздел: Выбор пола\n\nУкажите ваш пол:"
    await callback.message.edit_text(text=text, reply_markup=builder.as_markup())

async def save_gender(callback: types.CallbackQuery, gender_type: str, target_id: int):
    gender_value = "Мужской" if gender_type == "male" else "Женский"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE user_profiles SET gender = %s WHERE user_id = %s", (gender_value, target_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"open_edit_menu:{target_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text="Этот пол был успешно указан в вашем профиле", 
        reply_markup=builder.as_markup()
    )

async def open_desc_menu(callback: types.CallbackQuery, target_id: int):
    if callback.from_user.id != target_id:
        await callback.answer("Это меню принадлежит другому пользователю", show_alert=True)
        return

    await callback.answer()

    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data=f"cancel_desc:{target_id}")

    text = (
        "Раздел: Изменение описания\n\n"
        "Ответьте на это сообщение так:\n"
        "/change_description Моё описание (этот текст пример)\n\n"
        "Не превышайте лимит в 50 символов!"
    )
    await callback.message.edit_text(text=text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    
async def handle_profile_callbacks(callback: types.CallbackQuery):
    data_parts = callback.data.split(":")
    action = data_parts[0]
    
    if action in ["edit_profile", "open_edit_menu", "cancel_desc"]:
        target_id = int(data_parts[-1])
        await open_edit_menu(callback, target_id)
        
    elif action == "edit_gender":
        target_id = int(data_parts[-1])
        await open_gender_menu(callback, target_id)
        
    elif action == "set_gender":
        gender_type = data_parts[1]
        target_id = int(data_parts[-1])
        await save_gender(callback, gender_type, target_id)
        
    elif action == "edit_desc":
        target_id = int(data_parts[-1])
        await open_desc_menu(callback, target_id)
        
    elif action == "back_to_profile":
        target_id = int(data_parts[-1])
        await open_profile(callback, target_id)

async def save_description_from_text(message: types.Message, target_id: int, new_desc: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE user_profiles SET description = %s WHERE user_id = %s", (new_desc, target_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
        
    await message.answer("Описание было успешно изменено")

# 5. ОТМЕНА ВВОДА ОПИСАНИЯ
async def cancel_description_input(callback: types.CallbackQuery, target_id: int):
    if callback.from_user.id != target_id:
        await callback.answer("Это меню принадлежит другому пользователю", show_alert=True)
        return

    await callback.answer()
    
    from bot import user_states
    # Сбрасываем состояние ожидания, если нажата "Отмена"
    user_states.pop(target_id, None)

    # Возвращаем пользователя в меню редактирования профиля
    await open_edit_menu(callback, target_id)
