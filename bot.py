import asyncio
import os
from maxapi import Bot, Dispatcher
from maxapi.filters.command import Command
from maxapi.types import CallbackButton as B, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from catalog import connect
import filters as flt
import favorites as fav

bot = Bot(os.environ['MAX_BOT_TOKEN'])
dp = Dispatcher()
fav.init()


def keyboard(rows):
    kb = InlineKeyboardBuilder()
    for row in rows:
        if row:
            kb.row(*row)
    return [kb.as_markup()]


def card(tag_id, i, user_id, mode='card'):
    with connect() as db:
        if mode == 'favorite':
            saved_ids = fav.ids(user_id)
            total = len(saved_ids)
            if not total:
                return 'В избранном пока нет мероприятий.', keyboard([
                    [B(text='Каталог', payload='card:-1:0')],
                    [B(text='Фильтры', payload='filters:0')]])
            i = min(max(i, 0), total-1)
            e = db.execute('SELECT * FROM events WHERE id=?', (saved_ids[i],)).fetchone()
        else:
            e, total, i = flt.events(tag_id, i)
            if not total:
                return 'По фильтру мероприятий нет.', keyboard([
                    [B(text='Фильтры', payload='filters:0')]])

    text = (f"{e['title']}\n\n {e['date'] or 'Не указана'}\n"
            f"{e['place'] or 'Не указано'}\n\n{e['description'] or ''}")
    key = f'{mode}:{tag_id}:{i}' if mode == 'card' else f'favorite:{i}'
    nav = []
    if i:
        nav.append(B(text='⬅ Назад', payload=f'card:{tag_id}:{i-1}' if mode == 'card' else f'favorite:{i-1}'))
    if i < total-1:
        nav.append(B(text='Далее ➡', payload=f'card:{tag_id}:{i+1}' if mode == 'card' else f'favorite:{i+1}'))
    liked = fav.saved(user_id, e['id'])
    rows = [nav,
            [B(text='Убрать из избранного' if liked else 'В избранное',
               payload=f'toggle:{e["id"]}:{key}')],
            [LinkButton(text='Подробнее ↗', url=e['url'])],
            [B(text='Избранное', payload='favorite:0'),
             B(text='Фильтры', payload='filters:0')]]
    return text[:4000], keyboard(rows)


@dp.message_created(Command('filters'))
async def show_filters(event):
    text, kb = flt.menu()
    await event.message.answer(text=text, attachments=kb)


@dp.message_created(Command('start'))
@dp.message_created(Command('catalog'))
async def start(event):
    text, kb = card(-1, 0, event.message.sender.user_id)
    await event.message.answer(text=text, attachments=kb)


@dp.message_callback()
async def callback(event):
    parts = event.callback.payload.split(':')
    action = parts[0]
    user_id = event.callback.user.user_id
    if action == 'toggle':
        fav.toggle(user_id, int(parts[1]))
        parts = parts[2:]
        action = parts[0]
    if action == 'filters':
        text, kb = flt.menu(int(parts[1]))
    elif action == 'pick':
        text, kb = card(int(parts[1]), 0, user_id)
    elif action == 'favorite':
        text, kb = card(-1, int(parts[1]), user_id, 'favorite')
    elif action == 'card':
        text, kb = card(int(parts[1]), int(parts[2]), user_id)
    else:
        await event.answer()
        return
    await event.answer()
    await event.message.edit(text=text, attachments=kb)


asyncio.run(dp.start_polling(bot))
