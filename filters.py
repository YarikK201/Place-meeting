"""Поиск мероприятий. Настройки каждого пользователя сохраняются в SQLite."""
import json
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from maxapi.types import CallbackButton as B
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from catalog import connect

GROUPS = {
    'art': ('🎭 Виды искусства', ['Балет', 'Выставки', 'Драмы', 'Комедии', 'Спектакли', 'Хореография', 'Мюзиклы']),
    'music': ('🎵 Музыка', ['Джаз', 'Живая музыка', 'Классика', 'Концерты', 'Поп-музыка', 'Рок', 'Шансон', 'Хип-хоп', 'Народная музыка']),
    'activity': ('✨ Активности', ['Лекции', 'Мастер-классы', 'Пешеходные экскурсии', 'Саморазвитие', 'Цирковое шоу', 'Шоу', 'Экскурсии', 'волонтёрство', 'Движение Первых']),
}
AGES = ['Детям до 3 лет', 'Детям от 3 до 6 лет', 'Детям от 7 до 11 лет', 'Детям от 12 до 15 лет', 'Детям от 16 до 18 лет']
AGE_LABELS = ['До 3 лет', 'От 3–6 лет', 'От 7–11 лет', 'От 12–15 лет', 'От 16–18 лет']
# Указываем город только для известных площадок. Неизвестные остаются без города.
CITIES = {
    'Музей-заповедник «Томская Писаница»': 'Яшкинский округ',
    'Театр Металлургов': 'Новокузнецк',
    'ДК «Алюминщик» (г. Новокузнецк)': 'Новокузнецк',
    'Дворец культуры и искусства (г. Ленинск-Кузнецкий)': 'Ленинск-Кузнецкий',
    'ЦДК г. Белово': 'Белово',
}
OTHER_CITY = ('Белово', 'Ленинск-Кузнецкий', 'Яшкинский округ')


def city(place):
    if not place or place == 'Кемеровская область — Кузбасс':
        return None
    if place in CITIES:
        return CITIES[place]
    if 'Новокузнецк' in place:
        return 'Новокузнецк'
    if 'Кемерово' in place or 'Кедровка' in place or 'Кемеровск' in place:
        return 'Кемерово'
    # Известные из каталога кемеровские площадки
    if place in {
        'Музыкальный театр Кузбасса им. Боброва', 'ГАУК «Театр драмы Кузбасса»',
        'Кузбасский центр искусств', 'ГАУК «Филармония Кузбасса»',
        'СРК «Арена»', 'Отдел истории Кузбасского краеведческого музея',
        'Отдел военной истории Кузбасского краеведческого музея',
        'Отдел природы Кузбасского краеведческого музея',
        'Театр кукол Кузбасса имени Аркадия Гайдара',
        'Кемеровский Дворец молодежи', 'Клуб «Эфир»',
        'Театр для детей и молодёжи', 'Камерный театр современной поэзии «ЛитерА на Советском»',
    }:
        return 'Кемерово'
    return None


def today():
    return __import__('datetime').datetime.now(ZoneInfo('Asia/Novokuznetsk')).date()


def init():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS event_filters (
            user_id INTEGER PRIMARY KEY, topics TEXT NOT NULL DEFAULT '[]',
            ages TEXT NOT NULL DEFAULT '[]', city TEXT NOT NULL DEFAULT '',
            period TEXT NOT NULL DEFAULT '', format TEXT NOT NULL DEFAULT ''
        )''')


def state(user_id):
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO event_filters(user_id) VALUES (?)', (user_id,))
        row = db.execute('SELECT * FROM event_filters WHERE user_id=?', (user_id,)).fetchone()
    return dict(row) | {'topics': json.loads(row['topics']), 'ages': json.loads(row['ages'])}


def update(user_id, field, value):
    assert field in ('topics', 'ages', 'city', 'period', 'format')
    value = json.dumps(value, ensure_ascii=False) if field in ('topics', 'ages') else value
    with connect() as db:
        db.execute(f'UPDATE event_filters SET {field}=? WHERE user_id=?', (value, user_id))


def reset(user_id):
    with connect() as db:
        db.execute('DELETE FROM event_filters WHERE user_id=?', (user_id,))


def kb(rows):
    builder = InlineKeyboardBuilder()
    for row in rows:
        if row:
            builder.row(*row)
    return [builder.as_markup()]


def btn(name, payload):
    return B(text=name, payload=payload)


def pairs(buttons):
    return [buttons[i:i+2] for i in range(0, len(buttons), 2)]


def available():
    with connect() as db:
        return {row[0] for row in db.execute('SELECT DISTINCT value FROM events, json_each(events.tags)')}


def summary(s):
    def shorten(values, default):
        return ', '.join(values[:2]) + (f' +{len(values)-2}' if len(values)>2 else '') if values else default
    age_names = [AGE_LABELS[AGES.index(a)] for a in s['ages'] if a in AGES]
    periods = {'today':'Сегодня','tomorrow':'Завтра','week':'Эта неделя','weekend':'Выходные',
               'next7':'Следующие 7 дней','anytime':'Без точной даты'}
    return ('🔎 Настроим поиск!\nВыбери один или несколько параметров.\n\nСейчас выбрано:\n'
            f'🎭 Тематика: {shorten(s["topics"], "Любая")}\n'
            f'🎂 Возраст: {shorten(age_names, "Любой")}\n'
            f'📍 Место: {s["city"] or "Весь Кузбасс"}\n'
            f'📅 Дата: {periods.get(s["period"], s["period"] or "Любая")}\n'
            f'🌐 Формат: {s["format"] or "Любой"}')


def screen(user_id, view='home', param=None):
    s = state(user_id)
    if view == 'home':
        return summary(s), kb([
            [btn('🎭 Тематика','f:topic'),btn('🎂 Возраст','f:age')],
            [btn('📍 Место','f:city'),btn('📅 Дата','f:date')],
            [btn('🌐 Формат','f:format')],
            [btn('🔎 Показать мероприятия','f:results')],
            [btn('✕ Сбросить','f:clear:all'),btn('◀ Назад','card:-1:0')],
        ])
    if view == 'topic':
        return '🎭 Что тебе интересно?\nВыбери направления, затем конкретные темы.', kb([
            [btn(v[0],f'f:group:{k}')] for k,v in GROUPS.items()
        ] + [[btn('✅ Готово','f:home'),btn('✕ Очистить','f:clear:topics')],
             [btn('◀ Назад','f:home')]])
    if view == 'group':
        title, options = GROUPS[param]
        valid = available()
        choices = [(idx,t) for idx,t in enumerate(options) if t in valid]
        rows = pairs([btn(('✓ ' if t in s['topics'] else '') + t, f'f:tag:{param}:{idx}') for idx,t in choices])
        return title + '\nМожно выбрать несколько вариантов.', kb(rows + [
            [btn('✅ Готово','f:home'),btn('◀ Назад','f:topic')]])
    if view == 'age':
        valid = available()
        rows = [[btn(('✓ ' if a in s['ages'] else '') + AGE_LABELS[i], f'f:agepick:{i}')]
                for i,a in enumerate(AGES) if a in valid]
        return '🎂 Для какого возраста ищем?', kb(rows + [
            [btn('✅ Готово','f:home'), btn('✕ Очистить','f:clear:ages')], [btn('◀ Назад','f:home')]])
    if view == 'city':
        rows = [[btn(('✓ ' if s['city']==c else '') + c, f'f:citypick:{i}')]
                for i,c in enumerate(['Кемерово','Новокузнецк','Другие города','Весь Кузбасс'])]
        return '📍 Где хочешь найти мероприятие?', kb(rows+[[btn('◀ Назад','f:home')]])
    if view == 'other':
        rows = [[btn(('✓ ' if s['city']==c else '') + c,f'f:citypick:{i+4}')]
                for i,c in enumerate(OTHER_CITY)]
        return '📍 Другие города', kb(rows+[[btn('◀ Назад','f:city')]])
    if view == 'date':
        periods = [('Сегодня','today'),('Завтра','tomorrow'),('Эта неделя','week'),
                   ('Выходные','weekend'),('Следующие 7 дней','next7'),
                   ('📆 Выбрать дату','choose'),('Без точной даты','anytime')]
        return '📅 Когда хочешь пойти?', kb([
            [btn(('✓ ' if s['period']==value else '')+name, f'f:datepick:{value}')]
            for name,value in periods] + [
            [btn('✅ Готово','f:home'),btn('✕ Очистить','f:clear:period')],
            [btn('◀ Назад','f:home')]])
    if view == 'year':
        y = today().year
        return '📆 Выбери год:', kb([[btn(str(year),f'f:year:{year}')]
                                      for year in (y,y+1)] + [[btn('◀ Назад','f:date')]])
    if view == 'month':
        year = int(param)
        return f'📆 {year}: выбери месяц', kb(pairs([
            btn(f'{month:02}', f'f:month:{year}:{month}') for month in range(1,13)]) + [[btn('◀ Назад','f:year')]])
    if view == 'day':
        import calendar
        year,month = map(int,param.split('-'))
        last = calendar.monthrange(year, month)[1]
        return f'📆 {month:02}.{year}: выбери день', kb(pairs([
            btn(str(day),f'f:day:{year}-{month:02}-{day:02}') for day in range(1,last+1)]) + [[btn('◀ Назад',f'f:month:{year}:{month}')]])
    if view == 'format':
        options = [('🏫 Лично','офлайн'),('💻 Онлайн','онлайн'),('🌍 Любой формат','')]
        return '🌐 Как хочешь участвовать?', kb([
            [btn(('✓ ' if s['format']==v else '')+label,f'f:formatpick:{v or "any"}')]
            for label,v in options] + [[btn('◀ Назад','f:home')]])


def event_dates(value):
    """ISO дата, диапазон ISO-дат, 'с ДД.ММ.ГГГГ' и бессрочно."""
    if not value:
        return None, None
    if 'бессроч' in value.lower():
        return None, None
    iso = re.findall(r'20\d\d-\d\d-\d\d', value)
    if iso:
        try:
            return date.fromisoformat(iso[0]), date.fromisoformat(iso[-1])
        except ValueError:
            return None, None
    start = re.match(r'\s*с\s+(\d{2})\.(\d{2})\.(20\d{2})',value.lower())
    if start:
        d,m,y=map(int,start.groups())
        try:
            return date(y,m,d),None
        except ValueError:
            pass
    return None, None


def window(period):
    d=today()
    if not period:
        return None
    if period=='today': return d,d
    if period=='tomorrow': return d+timedelta(days=1),d+timedelta(days=1)
    if period=='week': return d,d+timedelta(days=6-d.weekday())
    if period=='next7': return d,d+timedelta(days=6)
    if period=='weekend':
        sat=d+timedelta(days=(5-d.weekday())%7)
        return sat,sat+timedelta(days=1)
    if re.fullmatch(r'20\d\d-\d\d-\d\d',period):
        chosen=date.fromisoformat(period)
        return chosen,chosen
    return None


def matches(e,s):
    ts=set(json.loads(e['tags'] or '[]'))
    if s['topics'] and not ts.intersection(s['topics']): return False
    if s['ages'] and not ts.intersection(s['ages']): return False
    if s['city'] and city(e['place']) != s['city']: return False
    if s['format'] and (e['format'] or '').lower()!=s['format']: return False
    first,last=event_dates(e['date'])
    d=today()
    if last and last < d or (first and not last and first < d and not e['date'].lower().startswith('с ')):
        return False
    if s['period']=='anytime': return first is None or e['date'].lower().startswith('с ')
    w=window(s['period'])
    if w:
        if first is None: return False
        return first <= w[1] and (last or first) >= w[0] if last else (
            first <= w[1] if e['date'].lower().startswith('с ') else w[0] <= first <= w[1])
    return True


def results(user_id):
    s=state(user_id)
    with connect() as db:
        found=[e for e in db.execute('SELECT * FROM events') if matches(e,s)]
    def sort_key(e):
        start,_=event_dates(e['date'])
        return (start is None, start or date.max, e['id'])
    return sorted(found,key=sort_key)


def handle(user_id, parts):
    action = parts[1] if len(parts)>1 else 'home'
    if action in ('home','topic','age','city','other','date','format'):
        return screen(user_id, action)
    if action=='year': return screen(user_id,'month',parts[2])
    if action=='group': return screen(user_id,'group',parts[2])
    if action=='tag':
        key,i=parts[2],int(parts[3]); tag=GROUPS[key][1][i]
        s=state(user_id); selected=s['topics']
        if tag in selected: selected.remove(tag)
        else: selected.append(tag)
        update(user_id,'topics',selected)
        return screen(user_id,'group',key)
    if action=='agepick':
        a=AGES[int(parts[2])]; s=state(user_id); selected=s['ages']
        if a in selected: selected.remove(a)
        else: selected.append(a)
        update(user_id,'ages',selected)
        return screen(user_id,'age')
    if action=='clear':
        if parts[2]=='all': reset(user_id)
        else: update(user_id,parts[2], [] if parts[2] in ('topics','ages') else '')
        return screen(user_id,'home' if parts[2]=='all' else 'topic' if parts[2]=='topics' else 'age' if parts[2]=='ages' else 'date')
    if action=='citypick':
        i=int(parts[2]); options=['Кемерово','Новокузнецк','Другие города','']+list(OTHER_CITY)
        if i==2: return screen(user_id,'other')
        update(user_id,'city', options[i]); return screen(user_id,'home')
    if action=='datepick':
        if parts[2]=='choose': return screen(user_id,'year')
        update(user_id,'period',parts[2]); return screen(user_id,'date')
    if action=='formatpick':
        update(user_id,'format','' if parts[2]=='any' else parts[2]); return screen(user_id,'home')
    if action=='month':
        return screen(user_id,'day',f'{int(parts[2])}-{int(parts[3]):02}')
    if action=='day':
        update(user_id,'period',parts[2]); return screen(user_id,'home')
    return screen(user_id,'home')


def results_unfiltered():
    """Все актуальные события без пользовательских ограничений."""
    with connect() as db:
        found = [e for e in db.execute('SELECT * FROM events')
                 if matches(e, {'topics': [], 'ages': [], 'city': '', 'format': '', 'period': ''})]
    return sorted(found, key=lambda e: (event_dates(e['date'])[0] is None,
                                        event_dates(e['date'])[0] or date.max, e['id']))
