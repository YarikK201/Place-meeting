
import asyncio
import os
from maxapi import Bot, Dispatcher
from maxapi.filters.command import Command
from maxapi.types import CallbackButton, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from catalog import connect

bot = Bot(os.environ["MAX_BOT_TOKEN"])
dp = Dispatcher()

def tags():
    with connect() as db:
        return [r[0] for r in db.execute("""
            SELECT DISTINCT value FROM events, json_each(events.tags)
            ORDER BY value
        """)]

def keyboard(buttons):
    kb = InlineKeyboardBuilder()
    for row in buttons:
        kb.row(*row)
    return [kb.as_markup()]

def filters(page=0):
    all_tags = tags()
    buttons = [
        [CallbackButton(text=t, payload=f"pick:{i}")]
        for i, t in enumerate(all_tags[page*8:page*8+8], page*8)
    ]
    nav = []
    if page > 0:
        nav.append(CallbackButton(text="⬅", payload=f"filters:{page-1}"))
    if (page+1)*8 < len(all_tags):
        nav.append(CallbackButton(text="➡", payload=f"filters:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([CallbackButton(text="Все мероприятия", payload="card:-1:0")])
    return "Выбери интересующую категорию:", keyboard(buttons)

def card(tag_id, i):
    all_tags = tags()
    tag = all_tags[tag_id] if tag_id >= 0 else None

    with connect() as db:
        sql = "FROM events e WHERE (? IS NULL OR EXISTS (SELECT 1 FROM json_each(e.tags) WHERE value = ?))"
        args = (tag, tag)
        total = db.execute("SELECT COUNT(*) " + sql, args).fetchone()[0]

        if not total:
            return "По этому фильтру мероприятий нет.", keyboard([
                [CallbackButton(text="Фильтры", payload="filters:0")]
            ])

        i = max(0, min(i, total-1))
        e = db.execute("SELECT e.* " + sql + " ORDER BY e.date, e.id LIMIT 1 OFFSET ?", (*args, i)).fetchone()

    text = (f"📅 {e['title']}\n\n"
            f"🕒 {e['date'] or 'Не указана'}\n"
            f"📍 {e['place'] or 'Не указано'}\n\n"
            f"{e['description'] or ''}")

    nav = []
    if i > 0:
        nav.append(CallbackButton(text="⬅ Назад", payload=f"card:{tag_id}:{i-1}"))
    if i < total-1:
        nav.append(CallbackButton(text="Далее ➡", payload=f"card:{tag_id}:{i+1}"))

    buttons = []
    if nav:
        buttons.append(nav)
    buttons.append([LinkButton(text="Подробнее ↗", url=e["url"])])
    buttons.append([CallbackButton(text="Фильтры", payload="filters:0")])
    return text, keyboard(buttons)

@dp.message_created(Command("filters"))
async def show_filters(event):
    text, buttons = filters()
    await event.message.answer(text=text, attachments=buttons)

@dp.message_created(Command("start"))
@dp.message_created(Command("catalog"))
async def start(event):
    text, buttons = card(-1, 0)
    await event.message.answer(text=text, attachments=buttons)

@dp.message_callback()
async def callback(event):
    parts = event.callback.payload.split(":")

    if parts[0] == "filters":
        text, buttons = filters(int(parts[1]))
    elif parts[0] == "pick":
        text, buttons = card(int(parts[1]), 0)
    else:
        text, buttons = card(int(parts[1]), int(parts[2]))

    await event.answer()
    await event.message.edit(text=text, attachments=buttons)

asyncio.run(dp.start_polling(bot))
