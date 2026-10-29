import asyncio
import logging
import os
import aiohttp
import json
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

# ГЛАВНЫЕ НАСТРОЙКИ
TOKEN = "8553478446:AAGz3WNx7PF3NozPVNjV-4NWfnwNOp9aBlY"
ALLOWED_GROUP_ID = -1002019929433  # ID вашей группы (должен быть числом, обычно начинается с -100)
OWNER_ID = 5374445721              # Telegram ID владельца (число), которому бот будет слать заявки
OWNER_USERNAME = "@SlavaUmni"      # Юзернейм владельца для текста (опционально)
RENDER_URL = "https://onrender.com" 

# ИНИЦИАЛИЗАЦИЯ БОТА И ДИСПЕТЧЕРА
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Временные хранилища данных (в оперативной памяти)
user_cooldowns = {}  # Для таймера ограничений: {user_id: datetime}
user_states = {}     # Для отслеживания шагов пользователя в ЛС
forwarded_requests = {} # Для связи ответов админа с пользователями: {admin_message_id: user_id}
user_menus = {} # Для хранения ID старых меню {user_id: message_id}

# ЛОГИКА ДЛЯ ЛИЧНЫХ СООБЩЕНИЙ (ЛС)
# Фильтр для проверки, что сообщение написано в ЛС
def is_private(message: types.Message) -> bool:
    return message.chat.type == "private"

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: types.Message):
    await message.answer("Введите команду /I_want_group если хотите написать владельцу группы")

@dp.message(Command("I_want_group"), F.chat.type == "private")
async def cmd_want_group(message: types.Message):
    user_id = message.from_user.id
    
    # Проверка таймера на 2 часа (Абуз-фильтр)
    if user_id in user_cooldowns:
        remaining_time = user_cooldowns[user_id] - datetime.now()
        if remaining_time.total_seconds() > 0:
            # Переводим секунды в читаемый формат (минуты)
            minutes = int(remaining_time.total_seconds() // 60)
            await message.answer(f"Команда /I_want_group будет работать для вас только через 2 часа")
            return

    # Если таймер прошел или его нет, переводим в состояние ожидания причины
    user_states[user_id] = "waiting_reason"
    await message.answer("Почему вы хотите попасть в группу? Напишите")

# Хэндлер на любые текстовые сообщения в ЛС
@dp.message(F.chat.type == "private")
async def handle_private_messages(message: types.Message):
    user_id = message.from_user.id
    current_state = user_states.get(user_id)

    # Если пользователь находится на шаге ввода причины
    if current_state == "waiting_reason":
        reason_text = message.text
        user_username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
        
        # Ставим ограничение на 2 часа с этого момента
        user_cooldowns[user_id] = datetime.now() + timedelta(hours=2)
        # Сбрасываем состояние
        user_states.pop(user_id, None)
        
        # Отвечаем пользователю
        await message.answer("Дождитесь ответа от владельца группы")
        
        # Формируем сообщение для владельца группы
        admin_text = (
            f"Пользователь {user_username} прислал вам такое сообщение: {reason_text}\n\n"
            f"Принимаете ли вы его? Если да то впишите в свой ответ на его сообщение ссылку приглашения"
        )
        
        try:
            # Бот отправляет сообщение владельцу
            sent_message = await bot.send_message(chat_id=OWNER_ID, text=admin_text)
            # Запоминаем ID сообщения, чтобы понять на какую именно заявку ответит админ
            forwarded_requests[sent_message.message_id] = user_id
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение владельцу: {e}")
            
    else:
        # Если это любое другое сообщение или спам вне контекста команды
        await message.answer("Введите команду /I_want_group если хотите написать владельцу группы")


# Хэндлер для владельца: обработка ответа (Reply) на заявку
@dp.message(F.chat.type == "private", F.reply_to_message)
async def handle_admin_reply(message: types.Message):
    # Проверяем, что отвечает именно владелец
    if message.from_user.id != OWNER_ID:
        return
        
    reply_id = message.reply_to_message.message_id
    
    # Проверяем, есть ли такое сообщение в нашей базе заявок
    if reply_id in forwarded_requests:
        target_user_id = forwarded_requests[reply_id]
        admin_answer = message.text # Текст ответа владельца (его ссылка)
        
        try:
            # Отправляем ответ пользователю, который подавал заявку
            await bot.send_message(chat_id=target_user_id, text=admin_answer)
            
            # Уведомляем владельца, что всё прошло успешно
            user_username = forwarded_requests.get(reply_id) # или можно вытащить из логов
            await message.answer(f"Ваш ответ был передан этому пользователю")
            
            # Удаляем заявку из активных, так как ответ дан
            forwarded_requests.pop(reply_id, None)
        except Exception as e:
            await message.answer("Не удалось доставить сообщение пользователю (возможно он заблокировал бота)")
            logging.error(f"Ошибка отправки ответа пользователю: {e}")

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


@dp.callback_query(F.data.startswith("menu_"))
async def handle_menu_clicks(callback: types.CallbackQuery):
    # Разделяем callback_data на действие и ID владельца меню
    action_data, owner_id_str = callback.data.split(":")
    owner_id = int(owner_id_str)
    
    # Проверяем, принадлежит ли меню тому, кто нажал на кнопку
    if callback.from_user.id != owner_id:
        # Показываем всплывающую ошибку "Это меню принадлежит другому пользователю"
        await callback.answer("Это меню принадлежит другому пользователю", show_alert=True)
        return

    # Если нажал владелец, гасим часики анимации нажатия на кнопке (без всплывающих окон)
    await callback.answer()

    # Ниже идет перенаправление в новые файлы-модули
    if action_data == "menu_profile":
        import menu_profile
        await menu_profile.open_profile(callback)
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
