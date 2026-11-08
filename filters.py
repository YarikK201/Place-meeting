"""Фильтры мероприятий: хранение выбора, меню и поиск."""
import calendar
import json
import re
import time
from functools import lru_cache
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from maxapi.types import CallbackButton as B
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from catalog import connect
import event_tags as et
import profiles
import teacher_events as te

GROUPS = {key: value for key, value in et.GROUPS.items() if key != 'age'}
AGES = et.AGES
AGE_NAMES = ['До 3 лет', '3–6 лет', '7–11 лет', '12–15 лет', '16–18 лет']
PERIODS = [('Сегодня','today'), ('Завтра','tomorrow'), ('Эта неделя','week'),
           ('Выходные','weekend'), ('Следующие 7 дней','next7'),
           ('📆 Ввести дату','choose'), ('Без точной даты','anytime')]
CITY_PLACES = {
    'Новокузнецк': ['Театр Металлургов', 'ДК «Алюминщик» (г. Новокузнецк)'],
    'Белово': ['ЦДК г. Белово'],
    'Ленинск-Кузнецкий': ['Дворец культуры и искусства (г. Ленинск-Кузнецкий)'],
    'Яшкинский округ': ['Музей-заповедник «Томская Писаница»'],
    'Кемерово': ['Музыкальный театр Кузбасса им. Боброва', 'ГАУК «Театр драмы Кузбасса»',
        'Кузбасский центр искусств', 'ГАУК «Филармония Кузбасса»', 'СРК «Арена»',
        'Отдел истории Кузбасского краеведческого музея',
        'Отдел военной истории Кузбасского краеведческого музея',
        'Отдел природы Кузбасского краеведческого музея',
        'Театр кукол Кузбасса имени Аркадия Гайдара', 'Кемеровский Дворец молодежи',
        'Клуб «Эфир»', 'Театр для детей и молодёжи',
        'Камерный театр современной поэзии «ЛитерА на Советском»']
}
CITIES = ['Кемерово','Новокузнецк','Другие города','Весь Кузбасс','Белово','Ленинск-Кузнецкий','Яшкинский округ']
FIELDS = ('topics','ages','city','period','format')

def today():
    return datetime.now(ZoneInfo('Asia/Novokuznetsk')).date()

def init():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS event_filters (
            user_id INTEGER PRIMARY KEY, topics TEXT DEFAULT '[]', ages TEXT DEFAULT '[]',
            city TEXT DEFAULT '', period TEXT DEFAULT '', format TEXT DEFAULT '')""")

def state(uid):
    with connect() as db:
        row = db.execute('SELECT * FROM event_filters WHERE user_id=?', (uid,)).fetchone()
    if row is None:
        return dict(topics=[], ages=[], city='', period='', format='')
    s = dict(row)
    for field in ('topics', 'ages'):
        s[field] = et.normalize(json.loads(s[field]))
    return s


def update(uid, field, value):
    if field not in FIELDS: return
    if isinstance(value, list): value = json.dumps(et.normalize(value), ensure_ascii=False)
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO event_filters(user_id) VALUES (?)', (uid,))
        db.execute(f'UPDATE event_filters SET {field}=? WHERE user_id=?', (value,uid))

def reset(uid):
    with connect() as db:
        db.execute('DELETE FROM event_filters WHERE user_id=?', (uid,))

def kb(rows):
    k = InlineKeyboardBuilder()
    for row in rows:
        if row: k.row(*row)
    return [k.as_markup()]

def btn(label, value): return B(text=label, payload='f:'+value)
def rows2(items): return [items[i:i+2] for i in range(0,len(items),2)]

def options(title, entries, selected, back='home', multiple=False, clear=None):
    buttons = rows2([btn(('✓ ' if value in selected else '')+name, callback)
                     for name,value,callback in entries])
    bottom = [btn('✅ Готово','home')]
    if clear: bottom.append(btn('✕ Очистить','clear:'+clear))
    return title, kb(buttons+[bottom, [btn('◀ Назад',back)]])

def available():
    # Набор тегов меняется редко. Избегаем повторного json_each на каждом клике.
    now = time.monotonic()
    if now - _available_at[0] >= 3.0:
        with connect() as db:
            _available_values.clear()
            _available_values.update(r[0] for r in db.execute(
                "SELECT DISTINCT value FROM events, json_each(events.tags)"))
        _available_at[0] = now
    return _available_values.copy()

_available_at = [float('-inf')]
_available_values = set()


def period_label(value):
    if not value:
        return 'Не выбрана'
    if re.fullmatch(r'20\d\d-\d\d-\d\d', value):
        return date.fromisoformat(value).strftime('%d.%m.%Y')
    return dict((key, name) for name, key in PERIODS).get(value, value)

def screen(uid, view='home', param=None):
    s = state(uid)
    if view == 'home':
        def shown(items, default): return ', '.join(items[:2])+(' +'+str(len(items)-2) if len(items)>2 else '') if items else default
        age_names = [AGE_NAMES[AGES.index(x)] for x in s['ages'] if x in AGES]
        date_name = period_label(s['period']) if s['period'] else 'Любая'
        text = (('⚙️ Подстроим мир под твои интересы. Ну, хотя бы каталог.\n\nСейчас выбрано:\n' if profiles.get(uid) and profiles.get(uid)['role']=='student' and not te.is_moderator(uid) else '🔎 Настроим поиск!\nВыбери нужные параметры.\n\nСейчас выбрано:\n')+
                f'🎭 Тематика: {shown(s["topics"],"Любая")}\n🎂 Возраст: {shown(age_names,"Любой")}\n'
                f'📍 Место: {s["city"] or "Весь Кузбасс"}\n📅 Дата: {date_name}\n🌐 Формат: {s["format"] or "Любой"}')
        return text,kb([[btn('🎭 Тематика','topic'),btn('🎂 Возраст','age')],
                        [btn('📍 Место','city'),btn('📅 Дата','date')], [btn('🌐 Формат','format')],
                        [btn('🔎 Показать мероприятия','results')],
                        [btn('✕ Сбросить','clear:all'), B(text='◀ Назад',payload='card:-1:0')]])
    if view == 'topic':
        return '🎭 Что тебе интересно?', kb([[btn(title,'group:'+key)] for key,(title,_) in GROUPS.items()]+
            [[btn('✅ Готово','home'),btn('✕ Очистить','clear:topics')],[btn('◀ Назад','home')]])
    if view == 'group':
        title,names=GROUPS[param]; valid=available()
        entries=[(name,name,f'tag:{param}:{i}') for i,name in enumerate(names)]
        return options(title,entries,s['topics'],'topic')
    if view == 'age':
        entries=[(AGE_NAMES[i],name,f'agepick:{i}') for i,name in enumerate(AGES)]
        return options('🎂 Для какого возраста ищем?',entries,s['ages'],clear='ages')
    if view in ('city','other'):
        indices = range(4,7) if view=='other' else range(4)
        entries=[(CITIES[i], CITIES[i] if i!=3 else '',f'citypick:{i}') for i in indices]
        return options('📍 Где ищем?', entries,[s['city']], 'city' if view=='other' else 'home')
    if view == 'date':
        # Для даты выбирается только один вариант, поэтому галочки не нужны.
        choices = [btn(name, 'datepick:' + value) for name, value in PERIODS]
        return (f'📅 Когда хочешь пойти?\nВыбранная дата: {period_label(s["period"])}',
                kb(rows2(choices) + [[btn('✕ Сбросить дату', 'clear:period')],
                                     [btn('◀ Назад', 'home')]]))
    if view == 'format':
        entries=[('🏫 Лично','офлайн','formatpick:офлайн'),('💻 Онлайн','онлайн','formatpick:онлайн'),
                 ('🌍 Любой формат','','formatpick:any')]
        return options('🌐 Как хочешь участвовать?',entries,[s['format']])
    if view == 'year':
        return options('📆 Выбери год',[(str(y),str(y),f'year:{y}') for y in (today().year,today().year+1)],[],'date')
    if view == 'month':
        y=int(param)
        return options('📆 Выбери месяц',[(f'{m:02}',str(m),f'month:{y}:{m}') for m in range(1,13)],[],'year')
    if view == 'day':
        y,m=map(int,param.split('-'))
        return options('📆 Выбери день',[(str(d),str(d),f'day:{y}-{m:02}-{d:02}') for d in range(1,calendar.monthrange(y,m)[1]+1)],[],f'month:{y}:{m}')

@lru_cache(maxsize=4096)
def _dates(value):
    if not value: return None,None
    found=re.findall(r'20\d\d-\d\d-\d\d',value)
    if found: return date.fromisoformat(found[0]),date.fromisoformat(found[-1])
    m=re.match(r'с\s+(\d\d)\.(\d\d)\.(20\d\d)',value.lower())
    if m:
        d,mo,y=map(int,m.groups())
        return date(y,mo,d),None
    return None,None

def dates(value):
    try:return _dates(value)
    except (ValueError,TypeError):return None,None


def concrete_date(value):
    start,end=dates(value)
    return start is not None and start==end and not value.lower().startswith('с ')


def window(period):
    d=today()
    if period=='today': return d,d
    if period=='tomorrow': return d+timedelta(days=1),d+timedelta(days=1)
    if period=='week': return d,d+timedelta(days=6-d.weekday())
    if period=='next7': return d,d+timedelta(days=6)
    if period=='weekend':
        sat=d+timedelta(days=(5-d.weekday())%7)
        return sat,sat+timedelta(days=1)
    if re.fullmatch(r'20\d\d-\d\d-\d\d',period):
        d=date.fromisoformat(period);return d,d

@lru_cache(maxsize=512)
def city(place):
    if not place or place=='Кемеровская область — Кузбасс': return ''
    for name,places in CITY_PLACES.items():
        if place in places: return name
    for name in ('Кемерово','Новокузнецк','Белово','Ленинск-Кузнецкий'):
        if name in place: return name
    if 'Кедровка' in place or 'Кемеровск' in place: return 'Кемерово'
    return ''

@lru_cache(maxsize=4096)
def parsed_tags(tags):
    return frozenset(et.normalize(tags))

def matches(e,s):
    if e['status']=='hidden': return False
    tags=parsed_tags(e['tags'] or '[]')
    # Если выбраны теги, достаточно совпадения хотя бы с одним из них.
    # Остальные совпадения учитываются при сортировке результатов.
    if (s['topics'] or s['ages']) and tags.isdisjoint(s['topics'] + s['ages']):
        return False
    if s['city'] and city(e['place'])!=s['city']: return False
    if s['format'] and (e['format'] or '').lower()!=s['format']: return False
    start,end=dates(e['date'] or '')
    indefinite=(e['date'] or '').lower().startswith('с ')
    if end and end < today() and not indefinite: return False
    if s['period']=='anytime': return not end or indefinite
    w=window(s['period'])
    if not w: return True
    if not start: return True
    return start<=w[1] and (end>=w[0] if end else indefinite or start>=w[0])

# Короткий кэш общего списка (0.8 c): соседние карточки не читают всю БД.
# Изменения модератора сбрасывают его немедленно через invalidate_events().
_events_cache = []
_events_at = [float('-inf')]
_result_cache = {}
_cache_epoch = [0]

def invalidate_events():
    _events_at[0] = float('-inf')
    _available_at[0] = float('-inf')
    _cache_epoch[0] += 1
    _result_cache.clear()

def all_events():
    if time.monotonic() - _events_at[0] >= 0.8:
        with connect() as db:
            _events_cache[:] = db.execute('SELECT * FROM events').fetchall()
        _events_at[0] = time.monotonic()
        _cache_epoch[0] += 1
        _result_cache.clear()
    return _events_cache

def tag_score(event, selected_tags):
    """Сколько выбранных тегов совпало с тегами мероприятия."""
    return len(parsed_tags(event['tags'] or '[]').intersection(selected_tags))


def results(uid=None):
    s = state(uid) if uid is not None else dict(topics=[], ages=[], city='', period='', format='')
    events = all_events()
    key = (uid, tuple(s['topics']), tuple(s['ages']), s['city'], s['period'],
           s['format'], _cache_epoch[0], today())
    if key not in _result_cache:
        selected_tags = set(s['topics'] + s['ages'])
        found = [e for e in events if matches(e, s)]
        # Сначала мероприятия с максимальным числом совпавших тегов.
        # При одинаковом числе совпадений — ближайшие по дате.
        found.sort(key=lambda e: (
            not concrete_date(e['date'] or '') if s['period'] else False,
            -tag_score(e, selected_tags),
            dates(e['date'] or '')[0] is None,
            dates(e['date'] or '')[0] or date.max,
            e['id'],
        ))
        if len(_result_cache) >= 512:
            _result_cache.clear()
        _result_cache[key] = found
    return _result_cache[key]

def results_unfiltered(): return results()

def handle(uid,parts):
    a=parts[1] if len(parts)>1 else 'home'
    if a in ('home','topic','age','city','other','date','format'): return screen(uid,a)
    if a=='group': return screen(uid,'group',parts[2])
    if a in ('tag','agepick'):
        field='topics' if a=='tag' else 'ages'
        item=GROUPS[parts[2]][1][int(parts[3])] if a=='tag' else AGES[int(parts[2])]
        selected=state(uid)[field]
        if item in selected: selected.remove(item)
        else: selected.append(item)
        update(uid,field,selected)
        return screen(uid,'group',parts[2]) if a=='tag' else screen(uid,'age')
    if a=='clear':
        field=parts[2]
        if field=='all': reset(uid); return screen(uid)
        update(uid,field,[] if field in ('topics','ages') else '')
        return screen(uid,{'topics':'topic','ages':'age','period':'date'}.get(field,'home'))
    if a=='citypick':
        i=int(parts[2])
        if i==2: return screen(uid,'other')
        update(uid,'city','' if i==3 else CITIES[i]);return screen(uid)
    if a=='datepick':
        if parts[2]=='choose': return ('📆 Введите дату в формате ДД.ММ.ГГ (например, 06.07.27)',kb([[btn('◀ Назад','date')]]))
        update(uid,'period',parts[2]);return screen(uid,'home')
    if a=='formatpick':
        update(uid,'format','' if parts[2]=='any' else parts[2]);return screen(uid)
    if a=='year': return screen(uid,'month',parts[2])
    if a=='month': return screen(uid,'day',f'{int(parts[2])}-{int(parts[3]):02}')
    if a=='day': update(uid,'period',parts[2]);return screen(uid)
    return screen(uid)
