from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

async def open_info(callback: types.CallbackQuery):
    # Извлекаем ID владельца меню из callback_data, который передал bot.py
    owner_id = callback.data.split(":")[1]
    
    # Создаем кнопку "Вернуться в меню"
    builder = InlineKeyboardBuilder()
    # Передаем в callback_data команду возврата и ID владельца
    builder.button(text="Вернуться в меню", callback_data=f"back_to_menu:{owner_id}")
    
    # Ваш точный текст для раздела информации
    info_text = (
        "Раздел: Информация\n\n"
        "Создатель бота: @votv_228\n"
        "Версия бота: v1.9.8\n"
        "Команды: /open_menu (доступно в этой группе) ; /I_want_group (доступно в ЛС бота) ; /plus_rep или /minus_rep {значение от 1 до 50} (ответьте на сообщение участника) ; /give_note {текст заметки} или /delete_note (доступны создателю бота) ; /view_profile {@username} (можете просто этой командой ответить на сообщение пользователя) ; /change_description (работает в разделе "Изменение описания")\n"
        "Названия команд говорят само за себя!\n\n"
        "Разработано на языке Python"
    )
    
    # Меняем текст меню на информацию и прикрепляем кнопку возврата
    await callback.message.edit_text(text=info_text, reply_markup=builder.as_markup())
