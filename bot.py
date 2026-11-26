import asyncio
import logging
import os
import aiohttp
import json
import re
import urllib.parse
import pg8000
import menu_profile
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

# ГЛАВНЫЕ НАСТРОЙКИ
TOKEN = "8553478446:AAGz3WNx7PF3NozPVNjV-4NWfnwNOp9aBlY"
ALLOWED_GROUP_ID = -1002019929433  # ID вашей группы (должен быть числом, обычно начинается с -100)
OWNER_ID = 7847585206
OWNER_USERNAME = "@votv_228"
RENDER_URL = "https://case-of-what-bot.onrender.com" 

# ИНИЦИАЛИЗАЦИЯ БОТА И ДИСПЕТЧЕРА
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Временные хранилища данных (в оперативной памяти)
user_menus = {} # Для хранения ID старых меню {user_id: message_id}

# ЛОГИКА ДЛЯ ЛИЧНЫХ СООБЩЕНИЙ (ЛС)
@dp.message(Command("attention_message"), F.chat.type == "private")
async def cmd_attention_message(message: types.Message):
    clean_owner_username = OWNER_USERNAME.lstrip("@").lower()
    user_username = (message.from_user.username or "").lower()

    # Проверка по ID и по username одновременно
    if message.from_user.id != OWNER_ID or user_username != clean_owner_username:
        await message.answer("Эта команда недоступна для вас")
        return

    # Извлекаем текст после команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Введите текст после команды /attention_message")
        return

    announcement_text = args[1].strip()

    try:
        # Отправляем от лица бота в основную тему (General) группы
        await bot.send_message(
            chat_id=ALLOWED_GROUP_ID,
            text=announcement_text,
            message_thread_id=None
        )
        await message.answer("Сообщение отправилось в группу")
    except Exception as e:
        logging.error(f"Ошибка при отправке объявления в группу: {e}")
        await message.answer("Не удалось отправить сообщение в группу, проверьте права бота.")

@dp.message(F.chat.type == "private")
async def handle_private_messages(message: types.Message):
    await message.answer("Бот работает только в группе Случай Чего, напишите владельцу группы о вступлении: @SlavaUmni")
    
# ЛОГИКА ПРОФИЛЕЙ
@dp.message(Command("view_profile"), F.chat.id == ALLOWED_GROUP_ID)
async def cmd_view_profile(message: types.Message):
    import menu_profile
    
    # Проверяем, что это настоящий Reply на сообщение человека, а не системная привязка к теме (Topic)
    is_real_reply = (
        message.reply_to_message is not None 
        and message.reply_to_message.forum_topic_created is None
    )
    
    # 1. Вариант через реальный Reply
    if is_real_reply:
        target_user = message.reply_to_message.from_user
        if target_user.is_bot:
            return
        await menu_profile.show_profile_by_reply(message, target_user, bot)
        return
        
    # 2. Вариант через @username
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        username_arg = args[1].strip().replace("@", "")
        if username_arg:
            await menu_profile.show_profile_by_username(message, username_arg, bot)
            return

    # 3. Если команду ввели пустой
    await message.reply("Ответьте на сообщения участника этой командой либо припишите команде @юзернейм участника")

# Команды изменения репутации (только по реплаю в группе)
@dp.message(
    Command("plus_rep", "minus_rep"), 
    F.chat.id == ALLOWED_GROUP_ID, 
    F.reply_to_message, 
    ~F.reply_to_message.forum_topic_created
)
async def cmd_change_reputation(message: types.Message):
    import menu_profile
    await menu_profile.handle_reputation_change(message)

# Команды управления заметками от Создателя бота (только по реплаю в группе)
@dp.message(
    Command("give_note", "delete_note"), 
    F.chat.id == ALLOWED_GROUP_ID, 
    F.reply_to_message, 
    ~F.reply_to_message.forum_topic_created
)
async def cmd_manage_notes(message: types.Message):
    import menu_profile
    await menu_profile.handle_note_management(message)

@dp.message(Command("moyastavka"), F.chat.id == ALLOWED_GROUP_ID)
async def cmd_moyastavka(message: types.Message):
    import menu_games
    await menu_games.process_dice_bet(message)
    
# ЛОГИКА ДЛЯ ГРУППЫ (МЕНЮ)
@dp.message(Command("open_menu"), F.chat.id == ALLOWED_GROUP_ID)
async def cmd_open_menu(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    # Если у пользователя уже было открыто меню, пытаемся его удалить
    if user_id in user_menus:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=user_menus[user_id])
        except Exception:
            # На случай, если прошло более 48 часов или сообщение уже удалено вручную
            pass

    builder = InlineKeyboardBuilder()
    # callback_data теперь хранит ID владельца меню через двоеточие, например: menu_profile:1234567
    builder.button(text="Профиль", callback_data=f"menu_profile:{user_id}")
    builder.button(text="Магазин", callback_data=f"menu_shop:{user_id}")
    builder.button(text="Развлечения", callback_data=f"menu_games:{user_id}")
    builder.button(text="Рейтинг", callback_data=f"menu_rating:{user_id}")
    builder.button(text="Информация", callback_data=f"menu_info:{user_id}")
    
    builder.adjust(2, 2, 1)
    
    menu_text = f"-- Для {user_name} --\nГлавное меню:"
    
    # Бот ИМЕННО ОТВЕЧАЕТ на сообщение пользователя с командой
    sent_menu = await message.answer(
        text=menu_text, 
        reply_markup=builder.as_markup(),
        reply_to_message_id=message.message_id
    )
    
    # Сохраняем ID нового меню в память
    user_menus[user_id] = sent_menu.message_id

@dp.callback_query()
async def handle_menu_clicks(callback: types.CallbackQuery):
    data = callback.data
    
    if ":" not in data:
        return
        
    data_parts = data.split(":")
    action_data = data_parts[0]
    owner_id = int(data_parts[-1])
    
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню принадлежит другому пользователю", show_alert=True)
        return
        
    await callback.answer()

    # Кнопка НАЗАД из профиля в главное меню бота
    if action_data == "back_to_menu":
        user_name = callback.from_user.first_name
        builder = InlineKeyboardBuilder()
        builder.button(text="Профиль", callback_data=f"menu_profile:{owner_id}")
        builder.button(text="Магазин", callback_data=f"menu_shop:{owner_id}")
        builder.button(text="Развлечения", callback_data=f"menu_games:{owner_id}")
        builder.button(text="Рейтинг", callback_data=f"menu_rating:{owner_id}")
        builder.button(text="Информация", callback_data=f"menu_info:{owner_id}")
        builder.adjust(2, 2, 1)
        
        await callback.message.edit_text(text=f"-- Для {user_name} --\nГлавное меню:", reply_markup=builder.as_markup())
        return

    # Корневые разделы
    if action_data == "menu_profile":
        import menu_profile
        await menu_profile.open_profile(callback, owner_id)
    elif action_data == "menu_shop":
        import menu_shop
        await menu_shop.open_shop(callback)
    elif action_data == "menu_games":
        import menu_games
        await menu_games.open_games(callback)
    elif action_data == "menu_rating":
        import menu_rating
        await menu_rating.open_rating(callback)
    elif action_data == "menu_info":
        import menu_info
        await menu_info.open_info(callback)
        
    # Перенаправление кнопок изменений профиля (включая Описание)
    if action_data in ["edit_profile", "open_edit_menu", "edit_gender", "set_gender", "edit_desc", "cancel_desc", "back_to_profile"]:
        import menu_profile
        await menu_profile.handle_profile_callbacks(callback)

    # Перенаправление кнопок раздела Развлечения
    if action_data in ["game_dice", "dice_cancel", "game_checkers", "game_scanner", "game_mansion", "game_cases", "game_minesweeper"]:
        import menu_games
        await menu_games.handle_games_callbacks(callback)

@dp.callback_query(F.data.startswith("back_to_menu:"))
async def handle_back_to_menu(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[1])
    if callback.from_user.id != owner_id:
        await callback.answer("Это меню принадлежит другому пользователю", show_alert=True)
        return
        
    await callback.answer()
    user_name = callback.from_user.first_name
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Профиль", callback_data=f"menu_profile:{owner_id}")
    builder.button(text="Магазин", callback_data=f"menu_shop:{owner_id}")
    builder.button(text="Развлечения", callback_data=f"menu_games:{owner_id}")
    builder.button(text="Рейтинг", callback_data=f"menu_rating:{owner_id}")
    builder.button(text="Информация", callback_data=f"menu_info:{owner_id}")
    builder.adjust(2, 2, 1)
    
    menu_text = f"-- Для {user_name} --\nГлавное меню:"
    
    await callback.message.edit_text(text=menu_text, reply_markup=builder.as_markup())

# СМЕНА ОПИСАНИЯ ЧЕРЕЗ КОМАНДУ ОТВЕТ
@dp.message(Command("change_description"), F.chat.id == ALLOWED_GROUP_ID)
async def cmd_change_description(message: types.Message):
    user_id = message.from_user.id
    
    # 1. Проверяем, что команда написана ответом (Reply) на сообщение инструкции бота
    if not message.reply_to_message:
        return

    # 2. Вытаскиваем ID хозяина профиля из инлайн-кнопки "Отмена"
    try:
        reply_markup = message.reply_to_message.reply_markup
        if not reply_markup or not reply_markup.inline_keyboard:
            return
            
        # Исправлено: корректный доступ к первой кнопке в двумерном массиве aiogram
        sample_callback = reply_markup.inline_keyboard[0][0].callback_data
        owner_id_from_msg = int(sample_callback.split(":")[-1])
    except Exception:
        owner_id_from_msg = user_id 

    if user_id != owner_id_from_msg:
        return

    # Извлекаем текст описания после команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Вы не ввели текст описания")
        return
        
    desc_text = args[1].strip()
    
    # ШТРАФ: Проверка лимита на 50 символов
    if len(desc_text) > 50:
        await message.reply("Описание не должно превышать 50 символов! Попробуйте снова")
        return

    # Чистим чат от команды и сообщения инструкции
    try:
        await message.delete()
        await message.reply_to_message.delete()
    except Exception:
        pass

    # Запись обновленного описания в Postgres таблицу
    import menu_profile
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE user_profiles SET description = %s WHERE user_id = %s", (desc_text, user_id))
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка записи описания в БД: {e}")
        return
    finally:
        cursor.close()
        conn.close()

    # Показываем финальный экран успеха с кнопкой Хорошо
    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"open_edit_menu:{user_id}")
    builder.adjust(1)

    await message.answer(
        text="Описание было успешно изменено", 
        reply_markup=builder.as_markup()
    )

# ПЕРЕХВАТ ТЕКСТОВЫХ СООБЩЕНИЙ В ГРУППЕ (ДЛЯ СТАВОК В ИГРАХ)
@dp.message(F.chat.id == ALLOWED_GROUP_ID, F.text)
async def handle_group_text_messages(message: types.Message):
    import menu_games
    await menu_games.process_dice_bet(message)

# ВЕБ-СЕРВЕР И AIOHTTP
async def handle_ping(request):
    return web.Response(text="Бот активен и работает!")

# ЗАПУСК БОТА
async def main():
    # 1. Запускаем мини-сервер для cron-job.org
    app = web.Application()
    app.router.add_get("/", handle_ping) # Слушаем главный адрес
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render автоматически передает нужный порт в переменные окружения
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    
    # Запускаем веб-сервер в фоне
    asyncio.create_task(site.start())
    logging.info(f"Фоновый веб-сервер для пинга запущен на порту {port}")
    
    # 2. Запускаем опрос Телеграма (Long Polling)
    logging.info(f"Бот Python 3.15.2 запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
