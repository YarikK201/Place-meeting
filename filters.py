from maxapi.types import CallbackButton as B
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from catalog import connect


def tags():
    with connect() as db:
        return [r[0] for r in db.execute(
            'SELECT DISTINCT value FROM events, json_each(events.tags) ORDER BY value')]


def menu(page=0):
    names = tags()
    rows = [[B(text=name, payload=f'pick:{i}')]
            for i, name in enumerate(names[page*8:page*8+8], page*8)]
    nav = []
    if page:
        nav.append(B(text='⬅', payload=f'filters:{page-1}'))
    if (page + 1)*8 < len(names):
        nav.append(B(text='➡', payload=f'filters:{page+1}'))
    if nav:
        rows.append(nav)
    rows += [[B(text='Все мероприятия', payload='card:-1:0')],
             [B(text='Избранное', payload='favorite:0')]]
    kb = InlineKeyboardBuilder()
    for row in rows:
        kb.row(*row)
    return 'Выбери интересующую категорию:', [kb.as_markup()]


def events(tag_id, index):
    names = tags()
    tag = names[tag_id] if 0 <= tag_id < len(names) else None
    sql = '''FROM events e WHERE (? IS NULL OR EXISTS (
        SELECT 1 FROM json_each(e.tags) WHERE value=?))'''
    with connect() as db:
        args = (tag, tag)
        total = db.execute('SELECT COUNT(*) ' + sql, args).fetchone()[0]
        if not total:
            return None, 0, 0
        index = max(0, min(index, total - 1))
        event = db.execute('SELECT e.* ' + sql + ' ORDER BY e.date,e.id LIMIT 1 OFFSET ?',
                           (*args, index)).fetchone()
        return event, total, index
