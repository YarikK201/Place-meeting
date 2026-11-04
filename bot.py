"""MAX-бот «Место встречи». Все обработчики объявлены ДО start_polling."""
import asyncio
import json
import os

from maxapi import Bot, Dispatcher, F
from maxapi.filters.command import Command
from maxapi.types import CallbackButton as B, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from catalog import connect
import favorites as fav
import filters as flt
import people
import profiles

bot = Bot(os.environ['MAX_BOT_TOKEN'])
dp = Dispatcher()
fav.init()
profiles.init()
flt.init()
people.init()

# Только ввод анкеты и ссылки требует текстового сообщения.
# Все остальные действия доступны через кнопки.
waiting_link = set()


def kb(rows):
    builder = InlineKeyboardBuilder()
    for row in rows:
        if row:
            builder.row(*row)
    return [builder.as_markup()]


def main_menu():
    return '🏠 Главное меню «Место встречи»', kb([
        [B(text='🎯 Мероприятия', payload='card:-1:0'),
         B(text='👥 Единомышленники', payload='p:menu')],
        [B(text='⭐ Избранное', payload='favorite:0'),
         B(text='⚙ Фильтры', payload='f:home')],
        [B(text='👤 Моя анкета', payload='my_profile'),
         B(text='📩 Заявки', payload='p:requests')],
    ])


def my_profile(user_id):
    p = profiles.get(user_id)
    if not p:
        return '👤 Анкета ещё не заполнена. Выбери роль:', kb([
            [B(text='🎒 Я школьник', payload='student'),
             B(text='👩‍🏫 Я педагог', payload='teacher')],
            [B(text='🏠 Главное меню', payload='home')],
        ])
    role = '🎒 Школьник' if p['role'] == 'student' else '👩‍🏫 Педагог'
    settings = people.get_settings(user_id)
    visible = bool(settings and settings['visible'])
    has_link = bool(settings and settings['contact_link'])
    text = (f'👤 Моя анкета\n\nРоль: {role}\n\nО себе:\n{p["bio"] or "Не заполнено"}\n\n'
            f'В каталоге: {"опубликована" if visible else "скрыта"}\n'
            f'Ссылка MAX: {"добавлена" if has_link else "не добавлена"}')
    return text[:4000], kb([
        [B(text='✏ Изменить описание', payload='edit_bio')],
        [B(text='🌍 Опубликовать' if not visible else '🔒 Скрыть',
           payload='publish_confirm' if not visible else 'hide_profile')],
        [B(text='🔗 Указать ссылку MAX', payload='set_link')],
        [B(text='👥 Смотреть анкеты', payload='p:menu')],
        [B(text='🏠 Главное меню', payload='home')],
    ])


def event_card(index, user_id, mode='card'):
    if mode == 'favorite':
        ids = fav.ids(user_id)
        with connect() as db:
            found = [db.execute('SELECT * FROM events WHERE id=?', (id_,)).fetchone() for id_ in ids]
        found = [x for x in found if x]
    elif mode == 'result':
        found = flt.results(user_id)
    else:
        found = flt.results_unfiltered()
    if not found:
        return '🔎 Подходящих мероприятий нет.', kb([
            [B(text='⚙ Изменить фильтры', payload='f:home')],
            [B(text='🎯 Все мероприятия', payload='card:-1:0')],
            [B(text='🏠 Главное меню', payload='home')],
        ])
    index = max(0, min(index, len(found)-1))
    e = found[index]
    tags = ', '.join(json.loads(e['tags'] or '[]'))
    text = (f'🎯 {e["title"]}\n\n📅 {e["date"] or "Не указана"}\n'
            f'📍 {e["place"] or "Не указано"}\n🌐 {e["format"] or "Не указан"}\n\n'
            f'{e["description"] or ""}\n\n{tags}')
    liked = fav.saved(user_id, e['id'])
    key = f'favorite:{index}' if mode == 'favorite' else f'result:{index}' if mode == 'result' else f'card:-1:{index}'
    nav_action = 'favorite' if mode == 'favorite' else 'result' if mode == 'result' else 'card:-1'
    nav = []
    if index:
        nav.append(B(text='◀ Назад', payload=f'{nav_action}:{index-1}'))
    if index < len(found)-1:
        nav.append(B(text='Далее ▶', payload=f'{nav_action}:{index+1}'))
    return text[:4000], kb([
        [B(text='✓ В избранном' if liked else '♡ В избранное',
           payload=f'toggle:{e["id"]}:{key}')],
        [LinkButton(text='↗ Подробнее', url=e['url'])],
        nav,
        [B(text='⚙ Фильтры', payload='f:home'),
         B(text='👥 Люди', payload='p:menu')],
        [B(text='🏠 Главное меню', payload='home')],
    ])


async def notify(user_id, text, attachments=None):
    """Не останавливаем бота, если MAX временно не доставил уведомление.
    Заявка уже хранится в SQLite и видна в разделе «Заявки».
    """
    try:
        await bot.send_message(user_id=user_id, text=text, attachments=attachments)
    except Exception as exc:
        print(f'Уведомление {user_id} не доставлено: {exc}')


@dp.message_created(Command('start'))
async def start(event):
    user_id = event.message.sender.user_id
    if profiles.get(user_id):
        text, attachments = main_menu()
    else:
        text, attachments = my_profile(user_id)
    await event.message.answer(text=text, attachments=attachments)


@dp.message_created(Command('people'))
async def people_command(event):
    text, attachments = people.menu()
    await event.message.answer(text=text, attachments=attachments)


@dp.message_created(Command('catalog'))
async def catalog_command(event):
    text, attachments = event_card(0, event.message.sender.user_id)
    await event.message.answer(text=text, attachments=attachments)


@dp.message_created(Command('filters'))
async def filters_command(event):
    text, attachments = flt.screen(event.message.sender.user_id)
    await event.message.answer(text=text, attachments=attachments)


@dp.message_created(Command('profile'))
async def profile_command(event):
    text, attachments = my_profile(event.message.sender.user_id)
    await event.message.answer(text=text, attachments=attachments)


@dp.message_callback()
async def callback(event):
    parts = event.callback.payload.split(':')
    action = parts[0]
    user_id = event.callback.user.user_id
    text, attachments = None, None

    if action == 'home':
        text, attachments = main_menu()
    elif action in ('student', 'teacher'):
        profiles.choose(user_id, action)
        text = '✏ Напиши о себе одним сообщением (до 1000 символов).'
        attachments = kb([[B(text='◀ Моя анкета', payload='my_profile')]])
    elif action == 'edit_bio':
        p = profiles.get(user_id)
        if p:
            profiles.choose(user_id, p['role'])
            text = '✏ Отправь новое описание одним сообщением (до 1000 символов).'
            attachments = kb([[B(text='◀ Моя анкета', payload='my_profile')]])
        else:
            text, attachments = my_profile(user_id)
    elif action == 'my_profile':
        text, attachments = my_profile(user_id)
    elif action == 'publish_confirm':
        p = profiles.get(user_id)
        if not p or not p['bio'] or p['waiting']:
            text = 'Сначала заполни анкету.'
            attachments = kb([[B(text='👤 Моя анкета', payload='my_profile')]])
        else:
            text = ('🌍 Опубликовать анкету?\n\n'
                    f'Другие пользователи увидят твою роль и описание:\n\n{p["bio"][:1500]}\n\n'
                    'Ссылка на MAX будет передана только после твоего отдельного согласия на заявку.')
            attachments = kb([
                [B(text='✅ Да, опубликовать', payload='publish_yes')],
                [B(text='✕ Отмена', payload='my_profile')],
            ])
    elif action == 'publish_yes':
        ok = people.publish(user_id)
        text = '✅ Анкета опубликована.' if ok else 'Сначала заполни анкету.'
        attachments = kb([[B(text='👤 Моя анкета', payload='my_profile')],
                          [B(text='👥 Поиск людей', payload='p:menu')]])
    elif action == 'hide_profile':
        people.hide(user_id)
        text = '🔒 Анкета скрыта из поиска.'
        attachments = kb([[B(text='👤 Моя анкета', payload='my_profile')]])
    elif action == 'set_link':
        waiting_link.add(user_id)
        text = ('🔗 Отправь ссылку на свой профиль MAX следующим сообщением.\n'
                'Например: https://max.ru/имя_профиля\n'
                'Контакт не будет опубликован в карточке.')
        attachments = kb([[B(text='✕ Отмена', payload='cancel_link')]])
    elif action == 'cancel_link':
        waiting_link.discard(user_id)
        text, attachments = my_profile(user_id)
    elif action == 'p':
        kind = parts[1]
        if kind == 'menu':
            text, attachments = people.menu()
        elif kind in ('student', 'teacher'):
            text, attachments = people.card(user_id, kind, int(parts[2]))
        elif kind == 'requests':
            text, attachments = people.requests_screen(user_id)
        elif kind == 'like':
            owner, role, index = int(parts[2]), parts[3], int(parts[4])
            created = people.request_like(user_id, owner)
            if created:
                await notify(owner, '❤️ Твою анкету лайкнули! Открой раздел «📩 Заявки» в боте.',
                             people.kb([[B(text='📩 Посмотреть заявку', payload='p:requests')]]))
            text, attachments = people.card(user_id, role, index)
        elif kind in ('accept', 'reject'):
            sender = int(parts[2])
            msg, link = people.decide(user_id, sender, kind == 'accept')
            if link:
                await notify(sender, f'💬 Владелец анкеты согласился на общение!\n{link}')
            elif kind == 'reject' and msg.startswith('✕'):
                await notify(sender, 'Заявка на общение отклонена.')
            text, attachments = msg, people.kb([
                [B(text='📩 Следующая заявка', payload='p:requests')],
                [B(text='👤 Моя анкета', payload='my_profile')],
            ])
        else:
            text, attachments = people.menu()
    elif action == 'f':
        if len(parts) > 1 and parts[1] == 'results':
            text, attachments = event_card(0, user_id, 'result')
        else:
            text, attachments = flt.handle(user_id, parts)
    elif action == 'filters':
        text, attachments = flt.screen(user_id)
    elif action == 'toggle':
        fav.toggle(user_id, int(parts[1]))
        rest = parts[2:]
        mode = 'favorite' if rest[0] == 'favorite' else 'result' if rest[0] == 'result' else 'card'
        index = int(rest[-1])
        text, attachments = event_card(index, user_id, mode)
    elif action in ('favorite', 'result', 'card'):
        text, attachments = event_card(int(parts[-1]), user_id, action)
    else:
        text, attachments = 'Эта кнопка устарела. Вернись в главное меню.', kb([
            [B(text='🏠 Главное меню', payload='home')]])

    await event.answer()
    await event.message.edit(text=text, attachments=attachments)


@dp.message_created(F.message.body.text)
async def text_message(event):
    user_id = event.message.sender.user_id
    text = (event.message.body.text or '').strip()
    if not text or text.startswith('/'):
        return
    if user_id in waiting_link:
        if not people.save_link(user_id, text):
            await event.message.answer('Нужна корректная ссылка вида https://max.ru/имя_профиля')
            return
        waiting_link.discard(user_id)
        msg, attachments = my_profile(user_id)
        await event.message.answer(text='✅ Ссылка сохранена.\n\n' + msg, attachments=attachments)
        return
    if not profiles.waiting(user_id):
        return
    if len(text) > 1000:
        await event.message.answer('Описание слишком длинное. Максимум 1000 символов.')
        return
    profiles.save(user_id, text)
    msg, attachments = my_profile(user_id)
    await event.message.answer(text='✅ Анкета сохранена!\n\n' + msg, attachments=attachments)


if __name__ == '__main__':
    asyncio.run(dp.start_polling(bot))
