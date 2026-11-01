import asyncio
import os
import people
from maxapi import Bot, Dispatcher, F
from maxapi.filters.command import Command
from maxapi.types import CallbackButton as B, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from catalog import connect
import filters as flt
import favorites as fav
import profiles

bot = Bot(os.environ['MAX_BOT_TOKEN'])
dp = Dispatcher()
fav.init()
profiles.init()
flt.init()
people.init()


def keyboard(rows):
    kb = InlineKeyboardBuilder()
    for row in rows:
        if row:
            kb.row(*row)
    return [kb.as_markup()]


def card(tag_id, i, user_id, mode='card'):
    if mode == 'favorite':
        saved_ids = fav.ids(user_id)
        total = len(saved_ids)
        if not total:
            return '⭐ Здесь пока пусто. Сохраняй интересные мероприятия.', keyboard([
                [B(text='🎯 Найти мероприятия', payload='card:-1:0')],
                [B(text='⚙ Фильтры', payload='f:home')]])
        i = min(max(i, 0), total-1)
        with connect() as db:
            e = db.execute('SELECT * FROM events WHERE id=?', (saved_ids[i],)).fetchone()
    else:
        found = flt.results(user_id) if mode == 'result' else flt.results_unfiltered()
        total = len(found)
        if not total:
            return ('🔎 Пока ничего не нашли. Попробуй изменить параметры.', keyboard([
                [B(text='⚙ Изменить фильтры', payload='f:home')],
                [B(text='✕ Сбросить всё', payload='f:clear:all')],
		[B(text='👥 Найти единомышленников', payload='person:0')],
                [B(text='🎯 Все мероприятия', payload='card:-1:0')]]))
        i = min(max(i, 0), total-1)
        e = found[i]

    tags = ', '.join(__import__('json').loads(e['tags'] or '[]'))
    text = (f"🎯 {e['title']}\n\n📅 Когда: {e['date'] or 'Не указана'}\n"
            f"📍 Где: {e['place'] or 'Не указано'}\n"
            f"🌐 Формат: {e['format'] or 'Не указан'}\n\n"
            f"{e['description'] or ''}\n\n{tags}")
    key = f'favorite:{i}' if mode == 'favorite' else f'result:{i}' if mode == 'result' else f'card:-1:{i}'
    liked = fav.saved(user_id, e['id'])
    nav_action = 'favorite' if mode == 'favorite' else 'result' if mode == 'result' else 'card:-1'
    nav = []
    if i > 0:
        nav.append(B(text='◀ Назад', payload=f'{nav_action}:{i-1}'))
    if i < total-1:
        nav.append(B(text='Далее ▶', payload=f'{nav_action}:{i+1}'))
    rows = [
        [B(text='✓ В избранном' if liked else '♡ В избранное',
           payload=f'toggle:{e["id"]}:{key}'), LinkButton(text='↗ Подробнее', url=e['url'])],
        [B(text='⭐ Избранное', payload='favorite:0'), B(text='⚙ Фильтры', payload='f:home')],
        nav,
    ]
    if mode == 'result' and i == 0:
        text = f'✅ Нашли мероприятия! Подходящих событий: {total}\n\n' + text
    return text[:4000], keyboard(rows)


@dp.message_created(Command('filters'))
async def show_filters(event):
    text, kb = flt.screen(event.message.sender.user_id)
    await event.message.answer(text=text, attachments=kb)


@dp.message_created(Command('start'))
async def start(event):
    await event.message.answer(
        text='👋 Привет! Это «Место встречи»!\nДля начала расскажи, кто ты.',
        attachments=keyboard([[
            B(text='🎒 Я школьник', payload='student'),
            B(text='👩‍🏫 Я педагог', payload='teacher')
        ]]))


@dp.message_created(Command('catalog'))
async def catalog(event):
    text, kb = card(-1, 0, event.message.sender.user_id)
    await event.message.answer(text=text, attachments=kb)


@dp.message_created(Command('profile'))
async def profile(event):
    p = profiles.get(event.message.sender.user_id)
    if p:
        role = 'Школьник' if p['role'] == 'student' else 'Учитель'
        await event.message.answer(f"👤 Моя анкета\nРоль: {role}\nО себе:\n{p['bio'] or 'Пока не заполнено'}")
    else:
        await event.message.answer('Сначала заполни анкету через /start.')


@dp.message_callback()
async def callback(event):
    parts = event.callback.payload.split(':')
    action = parts[0]
    user_id = event.callback.user.user_id

    if await people.handle(event, bot):
        return

    if action == 'toggle':
        fav.toggle(user_id, int(parts[1]))
        parts = parts[2:]
        action = parts[0]

    if action in ('student', 'teacher'):
        profiles.choose(user_id, action)
        text, kb = 'Расскажи о себе: чем увлекаешься и какие проекты тебе интересны?', []
    elif action == 'my_profile':
        p = profiles.get(user_id)
        if p:
            role = 'Школьник' if p['role']=='student' else 'Учитель'
            text = f"👤 Моя анкета\nРоль: {role}\nО себе:\n{p['bio'] or 'Не заполнено'}"
        else:
            text = 'Анкета не найдена. Начни регистрацию через /start.'
        kb = keyboard([[B(text='🎯 Каталог', payload='card:-1:0')]])
    elif action == 'f':
        if parts[1] == 'results':
            text, kb = card(-1, 0, user_id, 'result')
        else:
            text, kb = flt.handle(user_id, parts)
    elif action == 'filters':
        text, kb = flt.screen(user_id)
    elif action == 'favorite':
        text, kb = card(-1, int(parts[1]), user_id, 'favorite')
    elif action == 'result':
        text, kb = card(-1, int(parts[1]), user_id, 'result')
    elif action == 'card':
        text, kb = card(-1, int(parts[-1]), user_id)
    else:
        await event.answer()
        return
    await event.answer()
    await event.message.edit(text=text, attachments=kb)


@dp.message_created(F.message.body.text)
async def save_profile(event):
    user_id = event.message.sender.user_id
    text = event.message.body.text.strip()
    if not profiles.waiting(user_id) or not text or text.startswith('/'):
        return
    if len(text) > 1000:
        await event.message.answer('Максимум 1000 символов. Попробуй ещё раз.')
        return
    profiles.save(user_id, text)
    await event.message.answer('✅ Анкета сохранена!', attachments=keyboard([[
        B(text='🎯 Каталог', payload='card:-1:0'),
        B(text='👤 Моя анкета', payload='my_profile')]]))


asyncio.run(dp.start_polling(bot))

@dp.message_created(Command('people'))
async def people_catalog(event):
    user_id = event.message.sender.user_id
    text, kb = people.show(user_id)
    await event.message.answer(text=text, attachments=kb)


@dp.message_created(Command('publish'))
async def publish_profile(event):
    user_id = event.message.sender.user_id
    p = profiles.get(user_id)

    if not p or not p['bio'] or p['waiting']:
        await event.message.answer('Сначала заполни анкету через /start.')
        return

    people.publish(user_id)
    await event.message.answer('✅ Твоя анкета опубликована в каталоге участников.')


@dp.message_created(Command('hide'))
async def hide_profile(event):
    people.hide(event.message.sender.user_id)
    await event.message.answer('🔒 Твоя анкета скрыта из каталога.')

@dp.message_created(Command('my_link'))
async def my_link(event):
    user_id = event.message.sender.user_id
    parts = event.message.body.text.split(maxsplit=1)

    if len(parts) < 2 or not people.set_link(user_id, parts[1].strip()):
        await event.message.answer(
            'Отправь /my_link и ссылку на свой профиль MAX.'
        )
        return

    await event.message.answer('✅ Ссылка сохранена.')
