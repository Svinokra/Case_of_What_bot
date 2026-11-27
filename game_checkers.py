from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
import menu_profile

# Ожидающие соперника:
# {host_id: {"chat_id": int, "thread_id": int | None, "msg_id": int, "host_name": str}}
checkers_lobbies = {}

# Активные партии:
# {game_id: dict_with_game_state}
checkers_games = {}

# Привязка игрока к текущей активной партии: {user_id: game_id}
player_active_game = {}

def give_coins(user_id: int, username: str, full_name: str, amount: int):
    menu_profile.get_or_create_profile(user_id, username, full_name)
    conn = menu_profile.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT coins FROM user_profiles WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        (current_coins,) = row if row else (0,)
        cursor.execute(
            "UPDATE user_profiles SET coins = %s WHERE user_id = %s",
            (current_coins + amount, user_id)
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def format_mention(user: types.User) -> str:
    return f"@{user.username}" if user.username else user.first_name


def create_initial_board():
    """Создаёт доску 8x8. Сверху (ряды 0..2) чёрные, снизу (ряды 5..7) белые."""
    board = [["" for _ in range(8)] for _ in range(8)]
    for r in range(8):
        for c in range(8):
            if (r + c) % 2 == 1:
                if r < 3:
                    board[r][c] = "b"  # чёрная обычная
                elif r > 4:
                    board[r][c] = "w"  # белая обычная
    return board


async def start_checkers_search(callback: types.CallbackQuery):
    user = callback.from_user
    owner_id = int(callback.data.split(":")[-1])

    if user.id in player_active_game:
        await callback.answer("Вы уже участвуете в активной партии!", show_alert=True)
        return

    if user.id in checkers_lobbies:
        await callback.answer("Вы уже ищете соперника!", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="Назад", callback_data=f"checkers_cancel:{owner_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text=(
            "Поиск соперника...\n\n"
            "Другой участник должен ответить на это сообщение командой /join_checkers чтобы присоединиться к вам"
        ),
        reply_markup=builder.as_markup()
    )

    checkers_lobbies[user.id] = {
        "chat_id": callback.message.chat.id,
        "thread_id": getattr(callback.message, "message_thread_id", None),
        "msg_id": callback.message.message_id,
        "host_user": user,
    }


async def cancel_checkers_search(callback: types.CallbackQuery):
    owner_id = int(callback.data.split(":")[-1])
    checkers_lobbies.pop(owner_id, None)
    import menu_games
    await menu_games.open_games(callback)


async def process_join_checkers(message: types.Message) -> bool:
    guest = message.from_user
    if not guest or guest.is_bot:
        return False

    # Проверяем, что это Reply на сообщение поиска соперника
    reply_msg = message.reply_to_message
    if not reply_msg or getattr(reply_msg, "forum_topic_created", None) is not None:
        return False

    # Ищем лобби по ID сообщения
    target_host_id = None
    for host_id, lobby in checkers_lobbies.items():
        if lobby["chat_id"] == message.chat.id and lobby["msg_id"] == reply_msg.message_id:
            target_host_id = host_id
            break

    if target_host_id is None:
        return False

    # Нельзя присоединиться к самому себе или если уже в другой игре
    if guest.id == target_host_id:
        return False
    if guest.id in player_active_game or target_host_id in player_active_game:
        return False

    lobby = checkers_lobbies.pop(target_host_id)
    host_user = lobby["host_user"]

    # Убираем кнопку "Назад" у сообщения поиска
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=lobby["chat_id"],
            message_id=lobby["msg_id"],
            reply_markup=None
        )
    except Exception:
        pass

    game_id = f"{host_user.id}_{guest.id}"
    game = {
        "game_id": game_id,
        "chat_id": message.chat.id,
        "white_id": host_user.id,
        "white_user": host_user,
        "black_id": guest.id,
        "black_user": guest,
        "turn": "w",          # 'w' (белые) или 'b' (чёрные)
        "move_num": 1,
        "board": create_initial_board(),
        "selected": None,     # (r, c) выбранной шашки
        "must_capture_from": None,  # (r, c) если идёт комбо-рубка одной шашкой
        "msg_id": None,
    }

    checkers_games[game_id] = game
    player_active_game[host_user.id] = game_id
    player_active_game[guest.id] = game_id

    # Отрисовываем доску и отправляем сообщение начала партии (функция будет в Части 2)
    text, markup = render_board_message(game)
    board_msg = await message.answer(text=text, reply_markup=markup)
    game["msg_id"] = board_msg.message_id

    import bot as main_bot
    main_bot.user_menus[host_user.id] = board_msg.message_id
    main_bot.user_menus[guest.id] = board_msg.message_id
    return True

def is_own_piece(piece: str, turn: str) -> bool:
    if not piece:
        return False
    return piece.lower() == turn


def is_enemy_piece(piece: str, turn: str) -> bool:
    if not piece:
        return False
    return piece.lower() != turn


def get_piece_moves(board, r: int, c: int):
    """
    Возвращает два словаря для шашки на (r, c):
    quiet_moves: {(to_r, to_c): None}
    captures: {(to_r, to_c): (captured_r, captured_c)}
    """
    piece = board[r][c]
    if not piece:
        return {}, {}

    turn = piece.lower()
    is_king = piece.isupper()
    quiet_moves = {}
    captures = {}

    directions = [(-1, -1), (-1, 1), (1, -1), (1, 1)]

    if not is_king:
        # Тихие ходы: белые идут вверх (dr = -1), чёрные вниз (dr = +1)
        forward_dr = -1 if turn == "w" else 1
        for dc in (-1, 1):
            nr, nc = r + forward_dr, c + dc
            if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == "":
                quiet_moves[(nr, nc)] = None

        # Рубка: во все 4 диагональные стороны
        for dr, dc in directions:
            mr, mc = r + dr, c + dc
            lr, lc = r + 2 * dr, c + 2 * dc
            if 0 <= lr < 8 and 0 <= lc < 8:
                if is_enemy_piece(board[mr][mc], turn) and board[lr][lc] == "":
                    captures[(lr, lc)] = (mr, mc)
    else:
        # Дамка: скользит по всем 4 диагоналям
        for dr, dc in directions:
            step = 1
            enemy_pos = None
            while True:
                nr, nc = r + dr * step, c + dc * step
                if not (0 <= nr < 8 and 0 <= nc < 8):
                    break

                cell = board[nr][nc]
                if enemy_pos is None:
                    if cell == "":
                        quiet_moves[(nr, nc)] = None
                    elif is_enemy_piece(cell, turn):
                        enemy_pos = (nr, nc)
                    else:
                        # Упёрлись в свою шашку
                        break
                else:
                    # Уже перепрыгнули одну вражескую шашку — ищем пустые клетки за ней
                    if cell == "":
                        captures[(nr, nc)] = enemy_pos
                    else:
                        # Упёрлись во вторую шашку подряд — дальше нельзя
                        break
                step += 1

    return quiet_moves, captures


def count_player_pieces(board, turn: str) -> int:
    total = 0
    for r in range(8):
        for c in range(8):
            if is_own_piece(board[r][c], turn):
                total += 1
    return total


def has_any_legal_move(board, turn: str) -> bool:
    for r in range(8):
        for c in range(8):
            if is_own_piece(board[r][c], turn):
                q, cap = get_piece_moves(board, r, c)
                if q or cap:
                    return True
    return False


def render_board_message(game: dict):
    turn = game["turn"]
    move_num = game["move_num"]
    board = game["board"]
    selected = game["selected"]
    must_from = game["must_capture_from"]
    game_id = game["game_id"]

    if turn == "w":
        player_mention = format_mention(game["white_user"])
        color_emoji = "⚪"
    else:
        player_mention = format_mention(game["black_user"])
        color_emoji = "⚫"

    header_text = f"Ход {move_num} {player_mention} {color_emoji}"
    if must_from is not None:
        header_text += "\n🔥 Комбо-рубка! Продолжайте рубить этой шашкой"

    # Считаем подсказки (✅ и 💥) для выбранной шашки
    quiet_targets = {}
    capture_targets = {}
    if selected is not None:
        sr, sc = selected
        q, cap = get_piece_moves(board, sr, sc)
        capture_targets = cap
        # Если идёт комбо-рубка, тихие ходы запрещены — можно только рубить дальше
        if must_from is None:
            quiet_targets = q

    builder = InlineKeyboardBuilder()
    for r in range(8):
        for c in range(8):
            cell = board[r][c]
            if (r, c) in capture_targets:
                symbol = "💥"
            elif (r, c) in quiet_targets:
                symbol = "✅"
            elif cell == "w":
                symbol = "⚪"
            elif cell == "W":
                symbol = "⬜"
            elif cell == "b":
                symbol = "⚫"
            elif cell == "B":
                symbol = "⬛"
            else:
                # Тёмная пустая клетка помечается точкой, светлая — пробелом
                symbol = "·" if (r + c) % 2 == 1 else " "

            builder.button(text=symbol, callback_data=f"ck_c:{r}:{c}:{game_id}")

    # 9-й ряд: кнопка "Сдаться"
    builder.button(text="Сдаться", callback_data=f"ck_surrender:{game_id}")

    # 8 рядов по 8 клеток + 1 ряд с кнопкой Сдаться
    builder.adjust(8, 8, 8, 8, 8, 8, 8, 8, 1)

    return header_text, builder.as_markup()

def cleanup_game(game: dict):
    game_id = game["game_id"]
    checkers_games.pop(game_id, None)
    player_active_game.pop(game["white_id"], None)
    player_active_game.pop(game["black_id"], None)


async def finish_game_win(callback: types.CallbackQuery, game: dict, winner_turn: str):
    cleanup_game(game)

    if winner_turn == "w":
        winner_user = game["white_user"]
        winner_emoji = "⚪"
    else:
        winner_user = game["black_user"]
        winner_emoji = "⚫"

    give_coins(winner_user.id, winner_user.username, winner_user.full_name, 20)
    winner_mention = format_mention(winner_user)

    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"menu_games:{winner_user.id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text=(
            f"{winner_mention} {winner_emoji} выиграл!\n\n"
            f"Победителю начисляется 20 СЧ койнов"
        ),
        reply_markup=builder.as_markup()
    )


async def handle_checkers_surrender(callback: types.CallbackQuery, game_id: str):
    game = checkers_games.get(game_id)
    if not game:
        await callback.answer("Эта партия уже завершена.", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (game["white_id"], game["black_id"]):
        await callback.answer("Вы не участвуете в этой партии!", show_alert=True)
        return

    await callback.answer()
    cleanup_game(game)

    if user_id == game["white_id"]:
        loser_user = game["white_user"]
        loser_emoji = "⚪"
        winner_user = game["black_user"]
    else:
        loser_user = game["black_user"]
        loser_emoji = "⚫"
        winner_user = game["white_user"]

    give_coins(winner_user.id, winner_user.username, winner_user.full_name, 6)
    loser_mention = format_mention(loser_user)

    builder = InlineKeyboardBuilder()
    builder.button(text="Хорошо", callback_data=f"menu_games:{winner_user.id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text=(
            f"{loser_mention} {loser_emoji} сдался!\n\n"
            f"Победителю начислено 6 СЧ койнов"
        ),
        reply_markup=builder.as_markup()
    )


async def handle_board_click(callback: types.CallbackQuery):
    data = callback.data

    if data.startswith("ck_surrender:"):
        game_id = data.split(":", 1)[1]
        await handle_checkers_surrender(callback, game_id)
        return

    if not data.startswith("ck_c:"):
        return

    parts = data.split(":")
    r = int(parts[1])
    c = int(parts[2])
    game_id = parts[3]

    game = checkers_games.get(game_id)
    if not game:
        await callback.answer("Эта партия уже завершена", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (game["white_id"], game["black_id"]):
        await callback.answer("Это не ваша партия", show_alert=True)
        return

    turn = game["turn"]
    active_player_id = game["white_id"] if turn == "w" else game["black_id"]
    if user_id != active_player_id:
        await callback.answer("Сейчас ход соперника", show_alert=True)
        return

    board = game["board"]
    selected = game["selected"]
    must_from = game["must_capture_from"]
    clicked_piece = board[r][c]

    # 1. Переключение на другую свою шашку (если не заблокированы в комбо-рубке)
    if is_own_piece(clicked_piece, turn):
        if must_from is not None and (r, c) != must_from:
            await callback.answer("Завершите серию взятий этой же шашкой", show_alert=True)
            return
        game["selected"] = (r, c)
        await callback.answer()
        text, markup = render_board_message(game)
        await callback.message.edit_text(text=text, reply_markup=markup)
        return

    # 2. Если ни одна шашка не выбрана — игнорируем клик по пустой клетке
    if selected is None:
        await callback.answer()
        return

    sr, sc = selected
    quiet_moves, captures = get_piece_moves(board, sr, sc)

    # 3. Выполнение рубки (💥)
    if (r, c) in captures:
        cap_r, cap_c = captures[(r, c)]
        piece = board[sr][sc]
        board[sr][sc] = ""
        board[cap_r][cap_c] = ""

        # Проверка превращения в дамку
        if piece == "w" and r == 0:
            piece = "W"
        elif piece == "b" and r == 7:
            piece = "B"
        board[r][c] = piece

        # Проверяем, остались ли у соперника шашки
        enemy_turn = "b" if turn == "w" else "w"
        if count_player_pieces(board, enemy_turn) == 0:
            await callback.answer()
            await finish_game_win(callback, game, turn)
            return

        # Проверяем комбо-рубку этой же шашкой с новой позиции (r, c)
        _, next_captures = get_piece_moves(board, r, c)
        if next_captures:
            game["selected"] = (r, c)
            game["must_capture_from"] = (r, c)
            await callback.answer("Комбо-рубка!")
            text, markup = render_board_message(game)
            await callback.message.edit_text(text=text, reply_markup=markup)
            return

        # Серия завершена — передаём ход сопернику
        game["selected"] = None
        game["must_capture_from"] = None
        game["turn"] = enemy_turn
        game["move_num"] += 1

        if not has_any_legal_move(board, enemy_turn):
            await callback.answer()
            await finish_game_win(callback, game, turn)
            return

        await callback.answer()
        text, markup = render_board_message(game)
        await callback.message.edit_text(text=text, reply_markup=markup)
        return

    # 4. Выполнение тихого хода (✅)
    if must_from is None and (r, c) in quiet_moves:
        piece = board[sr][sc]
        board[sr][sc] = ""

        # Проверка превращения в дамку
        if piece == "w" and r == 0:
            piece = "W"
        elif piece == "b" and r == 7:
            piece = "B"
        board[r][c] = piece

        enemy_turn = "b" if turn == "w" else "w"
        game["selected"] = None
        game["must_capture_from"] = None
        game["turn"] = enemy_turn
        game["move_num"] += 1

        if not has_any_legal_move(board, enemy_turn):
            await callback.answer()
            await finish_game_win(callback, game, turn)
            return

        await callback.answer()
        text, markup = render_board_message(game)
        await callback.message.edit_text(text=text, reply_markup=markup)
        return

    await callback.answer()
