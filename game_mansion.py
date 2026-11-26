import random
from datetime import datetime, timedelta
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
import menu_profile
import menu_games

# Активные забеги в особняке:
# {user_id: {"room": int, "collected": int, "pending_coins": int, "event": dict | None}}
mansion_sessions = {}

MONSTERS = [
    "Цечиган",
    "Тыц",
    "Олян",
    "Козлян",
    "Орлян",
    "Кунапачати",
    "Туманец",
    "Подожый",
    "Жеяга",
    "THE FOG",
    "Сухарик",
    "Ярик",
    "Кузя",
    "Балка",
    "Амальгама",
    "Бурмалда",
    "Обречённый",
]

# Заготовки комнат с выбором: (текст ситуации, правильная кнопка, неправильная кнопка, имя монстра при ошибке)
CHOICE_EVENTS = [
    (
        "Вы встречаете тыца и он спрашивает Как дела? Что вы ему ответите?",
        "Плохо", # 2 строка это всегда правильно
        "Хорошо", # 3 строка это всегда НЕПРАВИЛЬНО
        "Тыц",
    ),
    (
        "Вы встречаете Ярика и он предлагает вам пойти попить чай с ним",
        "Давай",
        "Не хочу",
        "Ярик",
    ),
    (
        "ЧИКЕН БОГ ХОЧЕТ ПРОВЕРИТЬ, НЕ ХЕЙТИТЕ ЛИ ВЫ ЧИКЕН ГАН?",
        "Дать ему проверить",
        "Иди в попен",
        "Цечиган",
    ),
    (
        "6040 почему просто смотрит на вас",
        "Съесть",
        "Убежать",
        "Сухарик",
    ),
    (
        "Вы увидели Балку",
        "Обернуться и побежать",
        "Смотреть на неё",
        "Балка",
    ),
]

def get_mansion_cooldown(user_id: int) -> bool:
    menu_games.ensure_cooldowns_table()
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT last_played FROM game_cooldowns WHERE user_id = %s AND game_name = 'mansion'",
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


def set_mansion_cooldown(user_id: int):
    menu_games.ensure_cooldowns_table()
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        now = datetime.now()
        cursor.execute("""
            INSERT INTO game_cooldowns (user_id, game_name, last_played)
            VALUES (%s, 'mansion', %s)
            ON CONFLICT (user_id, game_name) DO UPDATE SET last_played = EXCLUDED.last_played
        """, (user_id, now))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


async def open_mansion_intro(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])

    if get_mansion_cooldown(user.id):
        builder = InlineKeyboardBuilder()
        builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
        builder.adjust(1)

        await callback.message.edit_text(
            text="Вы можете зайти в особняк только через 1 час",
            reply_markup=builder.as_markup()
        )
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="Начать прохождение", callback_data=f"mansion_start:{owner_id}")
    builder.button(text="Назад", callback_data=f"menu_games:{owner_id}")
    builder.adjust(1)

    intro_text = (
        "Добро пожаловать в особняк!\n\n"
        "Заходите в одну из трёх дверей которая приведёт вас либо к опасной комнате либо к безопасной, "
        "в безопасных комнатах вы найдёте СЧ койны и их количество от 5 до 19, собирайте их и продвигайтесь дальше "
        "но если вам попалась опасная комната то вас моментально убьют и это проигрыш а собранные СЧ койны сгорят "
        "и мы отнимем у вас столько на балансе сколько у вас было собранно СЧ койнов по прохождению особняка, "
        "если у вас их меньше или 0 то они просто сгорят и ничего отнимать не будем (как в костях), "
        "ваша цель добраться до 10 комнаты в которой будет выход из особняка и ваши собранные по пути СЧ койны начисляться на баланс, "
        "ой я забыл что есть ещё и такие комнаты в которых вам нужно сделать правильный выбор чтобы выжить"
    )
    await callback.message.edit_text(text=intro_text, reply_markup=builder.as_markup())

async def start_mansion_run(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])

    if get_mansion_cooldown(user.id):
        builder = InlineKeyboardBuilder()
        builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
        builder.adjust(1)
        await callback.message.edit_text(
            text="Вы можете играть в особняк только через 1 час",
            reply_markup=builder.as_markup()
        )
        return

    # Создаём сессию прохождения: начинаем с 1-й комнаты
    mansion_sessions[user.id] = {
        "room": 1,
        "collected": 0,
        "pending_coins": 0,
        "monster": "",
    }
    await show_door_choice(callback, owner_id)


async def show_door_choice(callback: types.CallbackQuery, owner_id: int):
    session = mansion_sessions.get(owner_id)
    if not session:
        await open_mansion_intro(callback)
        return

    room = session["room"]
    # Вычисляем номера трёх дверей для текущей комнаты (1-3, 4-6, 7-9 и т.д.)
    first_door = (room - 1) * 3 + 1

    builder = InlineKeyboardBuilder()
    builder.button(text=f"Дверь {first_door}", callback_data=f"mansion_door:{owner_id}")
    builder.button(text=f"Дверь {first_door + 1}", callback_data=f"mansion_door:{owner_id}")
    builder.button(text=f"Дверь {first_door + 2}", callback_data=f"mansion_door:{owner_id}")
    builder.button(text="Уйти", callback_data=f"mansion_leave:{owner_id}")

    # 1 ряд по 3 кнопки (двери), 2 ряд 1 кнопка ("Уйти")
    builder.adjust(3, 1)

    await callback.message.edit_text(
        text="Выберите дверь:",
        reply_markup=builder.as_markup()
    )


async def collect_room_coins(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])
    session = mansion_sessions.get(owner_id)
    if not session:
        await open_mansion_intro(callback)
        return

    # Переносим найденные в комнате койны в общий мешок забега и переходим к следующей комнате
    session["collected"] += session["pending_coins"]
    session["pending_coins"] = 0
    session["room"] += 1

    await show_door_choice(callback, owner_id)


async def leave_mansion(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])
    session = mansion_sessions.pop(owner_id, None)

    collected = session["collected"] if session else 0
    set_mansion_cooldown(user.id)

    # Начисляем собранные койны на баланс, если игрок успел что-то собрать
    if collected > 0:
        menu_profile.get_or_create_profile(user.id, user.username, user.full_name)
        conn = menu_profile.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT coins FROM user_profiles WHERE user_id = %s", (user.id,))
            row = cursor.fetchone()
            (current_coins,) = row if row else (0,)
            cursor.execute(
                "UPDATE user_profiles SET coins = %s WHERE user_id = %s",
                (current_coins + collected, user.id)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text=f"Вы пошли назад собрав с собой {collected}",
        reply_markup=builder.as_markup()
    )

async def finish_mansion_death(callback: types.CallbackQuery, owner_id: int, monster_name: str, is_wrong_choice: bool = False):
    user = callback.from_user
    session = mansion_sessions.pop(owner_id, None)
    collected = session["collected"] if session else 0

    set_mansion_cooldown(user.id)
    menu_profile.get_or_create_profile(user.id, user.username, user.full_name)

    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT coins FROM user_profiles WHERE user_id = %s", (user.id,))
        row = cursor.fetchone()
        (current_coins,) = row if row else (0,)

        if collected > 0 and current_coins >= collected:
            new_coins = current_coins - collected
            cursor.execute("UPDATE user_profiles SET coins = %s WHERE user_id = %s", (new_coins, user.id))
            conn.commit()
            outcome_text = f"Проигрыш, мы отнимаем у вас {collected} СЧ койнов"
        else:
            outcome_text = "Проигрыш, собранные СЧ койны сгорели"
    finally:
        cursor.close()
        conn.close()

    if is_wrong_choice:
        header = f"Неправильный выбор, {monster_name} убил вас"
    else:
        header = f"Вы зашли в опасную комнату и вас убил {monster_name}"

    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text=f"{header}\n\n{outcome_text}",
        reply_markup=builder.as_markup()
    )


async def open_mansion_door(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])
    session = mansion_sessions.get(owner_id)

    if not session:
        await open_mansion_intro(callback)
        return

    room = session["room"]

    # ФИНАЛ: 10-я комната (выход из особняка)
    if room >= 10:
        last_coins = random.randint(5, 19)
        total_won = session["collected"] + last_coins
        mansion_sessions.pop(owner_id, None)
        set_mansion_cooldown(user.id)

        menu_profile.get_or_create_profile(user.id, user.username, user.full_name)
        conn = menu_profile.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT coins FROM user_profiles WHERE user_id = %s", (user.id,))
            row = cursor.fetchone()
            (current_coins,) = row if row else (0,)
            cursor.execute(
                "UPDATE user_profiles SET coins = %s WHERE user_id = %s",
                (current_coins + total_won, user.id)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        builder = InlineKeyboardBuilder()
        builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
        builder.adjust(1)

        win_text = (
            f"Вы добрались до 10 комнаты и сразу же забрали {last_coins} СЧ койнов и побежали к выходу\n\n"
            f"Выигрыш, собранные по прохождению СЧ койны начислялись на ваш баланс"
        )
        await callback.message.edit_text(text=win_text, reply_markup=builder.as_markup())
        return

    # Обычные комнаты (с 1 по 9): распределяем шанс события
    roll = random.randint(1, 100)

    # 1. Безопасная комната (50% шанс)
    if roll <= 50:
        found_coins = random.randint(5, 19)
        session["pending_coins"] = found_coins

        builder = InlineKeyboardBuilder()
        builder.button(text="Собрать", callback_data=f"mansion_collect:{owner_id}")
        builder.adjust(1)

        await callback.message.edit_text(
            text=f"Вы зашли в безопасную комнату и здесь лежат {found_coins} СЧ койнов",
            reply_markup=builder.as_markup()
        )

    # 2. Опасная комната с выбором из 2 вариантов (30% шанс)
    elif roll <= 80:
        situation, right_btn, wrong_btn, monster = random.choice(CHOICE_EVENTS)
        session["monster"] = monster

        buttons = [
            (right_btn, f"mansion_choice_ok:{owner_id}"),
            (wrong_btn, f"mansion_choice_bad:{owner_id}"),
        ]
        random.shuffle(buttons)  # Перемешиваем, чтобы правильная кнопка не была всегда первой

        builder = InlineKeyboardBuilder()
        for btn_text, cb_data in buttons:
            builder.button(text=btn_text, callback_data=cb_data)
        builder.adjust(2)

        text = (
            f"Вы зашли в опасную комнату но вы можете избежать смерти!\n\n"
            f"{situation}"
        )
        await callback.message.edit_text(text=text, reply_markup=builder.as_markup())

    # 3. Моментальная опасная комната (20% шанс)
    else:
        monster = random.choice(MONSTERS)
        await finish_mansion_death(callback, owner_id, monster, is_wrong_choice=False)


async def handle_mansion_right_choice(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])
    session = mansion_sessions.get(owner_id)

    if not session:
        await open_mansion_intro(callback)
        return

    found_coins = random.randint(5, 19)
    session["pending_coins"] = found_coins

    builder = InlineKeyboardBuilder()
    builder.button(text="Собрать", callback_data=f"mansion_collect:{owner_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text=f"Правильный выбор! Вы сможете собрать {found_coins} СЧ койнов",
        reply_markup=builder.as_markup()
    )


async def handle_mansion_wrong_choice(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])
    session = mansion_sessions.get(owner_id)
    monster = session["monster"] if session and session["monster"] else random.choice(MONSTERS)
    await finish_mansion_death(callback, owner_id, monster, is_wrong_choice=True)


async def handle_mansion_callbacks(callback: types.CallbackQuery):
    data = callback.data

    if data.startswith("game_mansion:"):
        await open_mansion_intro(callback)
    elif data.startswith("mansion_start:"):
        await start_mansion_run(callback)
    elif data.startswith("mansion_door:"):
        await open_mansion_door(callback)
    elif data.startswith("mansion_collect:"):
        await collect_room_coins(callback)
    elif data.startswith("mansion_leave:"):
        await leave_mansion(callback)
    elif data.startswith("mansion_choice_ok:"):
        await handle_mansion_right_choice(callback)
    elif data.startswith("mansion_choice_bad:"):
        await handle_mansion_wrong_choice(callback)
