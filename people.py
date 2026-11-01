
from urllib.parse import urlparse

from catalog import connect
from maxapi.types import CallbackButton as B
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def keyboard(rows):
    kb = InlineKeyboardBuilder()
    for row in rows:
        if row:
            kb.row(*row)
    return [kb.as_markup()]


def init():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS people_settings (
                user_id INTEGER PRIMARY KEY,
                visible INTEGER DEFAULT 0,
                contact_link TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS people_likes (
                from_id INTEGER,
                to_id INTEGER,
                PRIMARY KEY (from_id, to_id)
            );
        """)


def publish(user_id):
    with connect() as db:
        db.execute("""
            INSERT INTO people_settings(user_id, visible)
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET visible=1
        """, (user_id,))


def hide(user_id):
    with connect() as db:
        db.execute(
            "UPDATE people_settings SET visible=0 WHERE user_id=?",
            (user_id,)
        )


def set_link(user_id, link):
    url = urlparse(link)
    if url.scheme != 'https' or url.hostname != 'max.ru':
        return False

    with connect() as db:
        db.execute("""
            INSERT INTO people_settings(user_id, contact_link)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET contact_link=excluded.contact_link
        """, (user_id, link))
    return True


def show(user_id, index=0):
    with connect() as db:
        people = db.execute("""
            SELECT p.user_id, p.role, p.bio
            FROM profiles p
            JOIN people_settings s ON s.user_id=p.user_id
            WHERE s.visible=1 AND p.waiting=0
              AND p.bio!='' AND p.user_id!=?
            ORDER BY p.user_id
        """, (user_id,)).fetchall()

    if not people:
        return 'Пока нет опубликованных анкет.', keyboard([
            [B(text='📅 Каталог', payload='card:-1:0')]
        ])

    index = max(0, min(index, len(people)-1))
    person = people[index]
    role = '🎒 Школьник' if person['role'] == 'student' else '👨‍🏫 Учитель'

    text = (f"👤 Анкета {index+1}/{len(people)}\n"
            f"{role}\n\nО себе:\n{person['bio']}")

    nav = []
    if index > 0:
        nav.append(B(text='⬅ Назад', payload=f'person:{index-1}'))
    if index < len(people)-1:
        nav.append(B(text='Далее ➡', payload=f'person:{index+1}'))

    return text[:4000], keyboard([
        nav,
        [B(text='❤️ Нравится', payload=f'like:{person["user_id"]}:{index}')],
        [B(text='📅 Каталог', payload='card:-1:0')]
    ])


def like(from_id, to_id):
    if from_id == to_id:
        return False

    with connect() as db:
        result = db.execute("""
            INSERT OR IGNORE INTO people_likes VALUES (?, ?)
        """, (from_id, to_id))
        return result.rowcount == 1


async def handle(event, bot):
    parts = event.callback.payload.split(':')
    action = parts[0]
    user_id = event.callback.user.user_id

    if action == 'person':
        text, kb = show(user_id, int(parts[1]))
        await event.answer()
        await event.message.edit(text=text, attachments=kb)
        return True

    if action == 'like':
        owner_id = int(parts[1])
        index = int(parts[2])

        if like(user_id, owner_id):
            await bot.send_message(
                user_id=owner_id,
                text='❤️ Кому-то понравилась твоя анкета! Хочешь поделиться контактом?',
                attachments=keyboard([[
                    B(text='✅ Разрешить общение',
                      payload=f'allow:{user_id}')
                ]])
            )

        text, kb = show(user_id, index)
        await event.answer()
        await event.message.edit(text=text, attachments=kb)
        return True

    if action == 'allow':
        requester = int(parts[1])

        with connect() as db:
            liked = db.execute("""
                SELECT 1 FROM people_likes
                WHERE from_id=? AND to_id=?
            """, (requester, user_id)).fetchone()

            settings = db.execute("""
                SELECT contact_link FROM people_settings
                WHERE user_id=?
            """, (user_id,)).fetchone()

        if not liked:
            text = 'Запрос на общение не найден.'
        elif not settings or not settings['contact_link']:
            text = 'Сначала добавь ссылку на свой MAX через /my_link.'
        else:
            await bot.send_message(
                user_id=requester,
                text=f"💬 Владелец анкеты согласился на общение!\n\n{settings['contact_link']}"
            )
            text = '✅ Твоя ссылка отправлена заинтересовавшемуся пользователю.'

        await event.answer()
        await event.message.edit(text=text, attachments=[])
        return True

    return False
