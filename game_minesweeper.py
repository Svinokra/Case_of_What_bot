import asyncio
import random
from datetime import datetime, timedelta
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
import menu_profile
import menu_games
import menu_shop

# Активные партии Сапёра:
# {user_id: dict_with_board_state}
minesweeper_sessions = {}

TOTAL_MINES = 10

def get_minesweeper_cooldown(user_id: int) -> bool:
    menu_games.ensure_cooldowns_table()
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT last_played FROM game_cooldowns WHERE user_id = %s AND game_name = 'minesweeper'",
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


def set_minesweeper_cooldown(user_id: int):
    menu_games.ensure_cooldowns_table()
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        now = datetime.now()
        cursor.execute("""
            INSERT INTO game_cooldowns (user_id, game_name, last_played)
            VALUES (%s, 'minesweeper', %s)
            ON CONFLICT (user_id, game_name) DO UPDATE SET last_played = EXCLUDED.last_played
        """, (user_id, now))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def give_minesweeper_reward(user: types.User, amount: int = 25):
    menu_profile.get_or_create_profile(user.id, user.username, user.full_name)
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT coins FROM user_profiles WHERE user_id = %s", (user.id,))
        row = cursor.fetchone()
        (current_coins,) = row if row else (0,)
        cursor.execute(
            "UPDATE user_profiles SET coins = %s WHERE user_id = %s",
            (current_coins + amount, user.id)
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def generate_mines_and_numbers(safe_r: int, safe_c: int):
    """
    Расставляет 10 мин на поле 8x8 так, чтобы клетка (safe_r, safe_c) и её соседи были чистыми.
    Возвращает множество координат мин и матрицу чисел 8x8 (-1 = мина, 0..8 = число мин вокруг).
    """
    forbidden = set()
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            nr, nc = safe_r + dr, safe_c + dc
            if 0 <= nr < 8 and 0 <= nc < 8:
                forbidden.add((nr, nc))

    candidates = [(r, c) for r in range(8) for c in range(8) if (r, c) not in forbidden]
    mines = set(random.sample(candidates, TOTAL_MINES))

    numbers = [[0 for _ in range(8)] for _ in range(8)]
    for r in range(8):
        for c in range(8):
            if (r, c) in mines:
                numbers[r][c] = -1
                continue
            count = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if (r + dr, c + dc) in mines:
                        count += 1
            numbers[r][c] = count

    return mines, numbers


async def open_minesweeper_intro(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])

    if get_minesweeper_cooldown(user.id):
        builder = InlineKeyboardBuilder()
        builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
        builder.adjust(1)

        await callback.message.edit_text(
            text="Вы можете играть в сапёр только через 1 час",
            reply_markup=builder.as_markup()
        )
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="Начать", callback_data=f"ms_start:{owner_id}")
    builder.button(text="Назад", callback_data=f"menu_games:{owner_id}")
    builder.adjust(1, 1)

    intro_text = (
        "Добро пожаловать в игру Сапёр!\n\n"
        "Перед вами минное поле 8×8 на котором спрятано 10 мин\n"
        "Нажимайте на зелёные клетки 🟩 чтобы открывать безопасную территорию, первое нажатие всегда безопасное\n"
        "Цифры (1️⃣–8️⃣) показывают, сколько мин спрятано в соседних клетках вокруг этой цифры а вот жёлтые клетки 🟨 означают что рядом мин нет\n"
        "Чтобы пометить предполагаемую мину флажком 🚩 то переключите нижнюю кнопку в режим Ставить флажки ON (всего доступно 10 флажков)\n"
        "Откройте все 54 безопасные клетки и получите 25 СЧ койнов! Если подорвётесь на мине то игра закончится но ваши СЧ койны с баланса не снимутся"
    )
    await callback.message.edit_text(text=intro_text, reply_markup=builder.as_markup())

NUM_EMOJIS = {
    0: "🟨",
    1: "1️⃣",
    2: "2️⃣",
    3: "3️⃣",
    4: "4️⃣",
    5: "5️⃣",
    6: "6️⃣",
    7: "7️⃣",
    8: "8️⃣",
}


def reveal_cells(session: dict, start_r: int, start_c: int):
    """Открывает клетку и рекурсивно (через очередь) раскрывает пустую область вокруг."""
    revealed = session["revealed"]
    flagged = session["flagged"]
    numbers = session["numbers"]

    stack = [(start_r, start_c)]
    while stack:
        r, c = stack.pop()
        if (r, c) in revealed or (r, c) in flagged:
            continue

        revealed.add((r, c))

        if numbers[r][c] == 0:
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < 8 and 0 <= nc < 8:
                        if (nr, nc) not in revealed and (nr, nc) not in flagged:
                            stack.append((nr, nc))


def render_minesweeper_board(session: dict, owner_id: int, reveal_mode: str = "normal"):
    """
    reveal_mode:
      'normal'    - обычный ход
      'triggered' - взорвалась одна нажатая мина (остальные ещё скрыты)
      'all_bombs' - через 2 сек показались все остальные мины 💣
      'exploded'  - через 5 сек все мины взорвались 💥
    """
    revealed = session["revealed"]
    flagged = session["flagged"]
    mines = session["mines"]
    numbers = session["numbers"]
    triggered_mine = session["triggered_mine"]
    flag_mode = session["flag_mode"]

    flags_used = len(flagged)
    header_text = f"Осталось флагов: {flags_used}/{TOTAL_MINES}"

    builder = InlineKeyboardBuilder()
    for r in range(8):
        for c in range(8):
            pos = (r, c)
            if reveal_mode == "triggered" and pos == triggered_mine:
                symbol = "💣"
            elif reveal_mode == "all_bombs" and pos in mines:
                symbol = "💣"
            elif reveal_mode == "exploded" and pos in mines:
                symbol = "💥"
            elif pos in revealed:
                val = numbers[r][c]
                symbol = NUM_EMOJIS.get(val, "🟨")
            elif pos in flagged:
                symbol = "🚩"
            else:
                symbol = "🟩"

            builder.button(text=symbol, callback_data=f"ms_c:{r}:{c}:{owner_id}")

    # Нижняя кнопка переключателя режима флагов (9-й ряд)
    flag_btn_text = "Ставить флажки ON" if flag_mode else "Ставить флажки OFF"
    builder.button(text=flag_btn_text, callback_data=f"ms_flag_toggle:{owner_id}")

    builder.adjust(8, 8, 8, 8, 8, 8, 8, 8, 1)
    return header_text, builder.as_markup()

async def start_minesweeper_game(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])

    if get_minesweeper_cooldown(user.id):
        builder = InlineKeyboardBuilder()
        builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
        builder.adjust(1)
        await callback.message.edit_text(
            text="Вы можете играть в сапёр только через 1 час",
            reply_markup=builder.as_markup()
        )
        return

    session = {
        "initialized": False,   # Мины сгенерируются после первого клика
        "mines": set(),
        "numbers": [[0 for _ in range(8)] for _ in range(8)],
        "revealed": set(),
        "flagged": set(),
        "flag_mode": False,
        "locked": False,
        "triggered_mine": None,
    }
    minesweeper_sessions[owner_id] = session

    text, markup = render_minesweeper_board(session, owner_id)
    await callback.message.edit_text(text=text, reply_markup=markup)


async def handle_flag_toggle(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])
    session = minesweeper_sessions.get(owner_id)

    if not session:
        await open_minesweeper_intro(callback)
        return

    if session["locked"]:
        await callback.answer()
        return

    session["flag_mode"] = not session["flag_mode"]
    await callback.answer()

    text, markup = render_minesweeper_board(session, owner_id)
    await callback.message.edit_text(text=text, reply_markup=markup)


async def handle_minesweeper_cell(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    r = int(parts[1])
    c = int(parts[2])
    owner_id = int(parts[3])

    session = minesweeper_sessions.get(owner_id)
    if not session:
        await open_minesweeper_intro(callback)
        return

    if session["locked"]:
        await callback.answer()
        return

    pos = (r, c)

    # Нельзя нажимать на уже открытую клетку
    if pos in session["revealed"]:
        await callback.answer()
        return

    # РЕЖИМ УСТАНОВКИ/СНЯТИЯ ФЛАЖКОВ (ON)
    if session["flag_mode"]:
        if pos in session["flagged"]:
            session["flagged"].remove(pos)
        else:
            if len(session["flagged"]) >= TOTAL_MINES:
                await callback.answer("Вы уже поставили все 10 флажков", show_alert=True)
                return
            session["flagged"].add(pos)

        await callback.answer()
        text, markup = render_minesweeper_board(session, owner_id)
        await callback.message.edit_text(text=text, reply_markup=markup)
        return

    # ОБЫЧНЫЙ РЕЖИМ ОТКРЫТИЯ КЛЕТКИ (OFF)
    if pos in session["flagged"]:
        await callback.answer("Сначала уберите флажок с этой клетки", show_alert=True)
        return

    # Если это первый клик в партии — генерируем мины с защитой вокруг (r, c)
    if not session["initialized"]:
        mines, numbers = generate_mines_and_numbers(r, c)
        session["mines"] = mines
        session["numbers"] = numbers
        session["initialized"] = True

    # ПРОВЕРКА НА ПОПАДАНИЕ НА МИНУ
    if pos in session["mines"]:
        session["locked"] = True
        session["triggered_mine"] = pos
        set_minesweeper_cooldown(owner_id)
        await callback.answer("💥 Вы наткнулись на мину!")

        # Этап 1 (0 сек): показываем нажатую мину 💣
        text, markup = render_minesweeper_board(session, owner_id, reveal_mode="triggered")
        await callback.message.edit_text(text=text, reply_markup=markup)

        # Этап 2 (через 2 сек): проявляем все остальные скрытые мины 💣
        await asyncio.sleep(2.0)
        text, markup = render_minesweeper_board(session, owner_id, reveal_mode="all_bombs")
        await callback.message.edit_text(text=text, reply_markup=markup)

        # Этап 3 (ещё через 3 сек, итого 5 сек): все мины взрываются 💥
        await asyncio.sleep(3.0)
        text, markup = render_minesweeper_board(session, owner_id, reveal_mode="exploded")
        await callback.message.edit_text(text=text, reply_markup=markup)

        # Небольшая пауза, чтобы игрок увидел взрывы 💥, и финальный экран проигрыша
        await asyncio.sleep(1.5)
        minesweeper_sessions.pop(owner_id, None)

        builder = InlineKeyboardBuilder()
        builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
        builder.adjust(1)

        await callback.message.edit_text(
            text="Проигрыш, вы наткнулись на мину",
            reply_markup=builder.as_markup()
        )
        return

    # Безопасная клетка — раскрываем область
    reveal_cells(session, r, c)

    # ПРОВЕРКА ПОБЕДЫ (открыты все 64 - 10 = 54 безопасные клетки)
    if len(session["revealed"]) >= (64 - TOTAL_MINES):
        import menu_shop
        buffs = menu_shop.get_user_buffs(owner_id)
        reward = 50 if buffs["doubler"] else 25

        minesweeper_sessions.pop(owner_id, None)
        set_minesweeper_cooldown(owner_id)
        give_minesweeper_reward(callback.from_user, amount=reward)
        await callback.answer("🎉 Победа!")

        builder = InlineKeyboardBuilder()
        builder.button(text="Хорошо", callback_data=f"menu_games:{owner_id}")
        builder.adjust(1)

        await callback.message.edit_text(
            text=f"Выигрыш, вы открыли все клетки без мин, вам начислено {reward} СЧ койнов",
            reply_markup=builder.as_markup()
        )
        return

    await callback.answer()
    text, markup = render_minesweeper_board(session, owner_id)
    await callback.message.edit_text(text=text, reply_markup=markup)


async def handle_minesweeper_callbacks(callback: types.CallbackQuery):
    data = callback.data

    if data.startswith("game_minesweeper:"):
        await open_minesweeper_intro(callback)
    elif data.startswith("ms_start:"):
        await start_minesweeper_game(callback)
    elif data.startswith("ms_flag_toggle:"):
        await handle_flag_toggle(callback)
    elif data.startswith("ms_c:"):
        await handle_minesweeper_cell(callback)
