import html
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
import menu_profile

async def open_rating(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])

    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT username, full_name, coins
            FROM user_profiles
            WHERE coins > 0
            ORDER BY coins DESC, joined_date ASC
            LIMIT 26
        """)
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []

    for idx, (username, full_name, coins) in enumerate(rows, start=1):
        if username and username != "None":
            name_str = f"@{html.escape(username)}"
        else:
            name_str = html.escape(full_name or "Участник")

        prefix = medals[idx] if idx in medals else f"{idx}."
        lines.append(f"{prefix} {name_str} - {coins} СЧ койнов")

    if lines:
        quote_block = "<blockquote>" + "\n".join(lines) + "</blockquote>"
    else:
        quote_block = "<blockquote>Пока никто не заработал СЧ койны</blockquote>"

    text = (
        "Раздел: Рейтинг\n\n"
        "Топ 26 по самому большому количеству СЧ койнов:\n"
        f"{quote_block}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="Назад", callback_data=f"back_to_menu:{owner_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text=text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
