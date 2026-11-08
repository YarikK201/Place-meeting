"""«Место встречи»: кнопочный интерфейс событий, анкет, дружбы и модерации."""
import asyncio
import json
import os
import logging
from copy import deepcopy
from functools import wraps
from time import perf_counter, time
from uuid import uuid4
from maxapi.exceptions import MaxApiError
from collections import defaultdict, deque
from maxapi import Bot, Dispatcher, F
from maxapi.types import Command, CallbackButton as B, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from catalog import connect
import profiles, people, social, teacher_events as te, filters as flt, favorites as fav, role_requests as rr, direct_messages as dm
import event_tags as et
from presentation import Markdown, event_text, parse_teacher_date, escape

bot = Bot(os.environ['MAX_BOT_TOKEN'])
dp = Dispatcher()
LOG = logging.getLogger("place_meeting.ui")

# Подготавливаем таблицы один раз при запуске.
for module in (profiles, social, people, te, flt, fav, rr, dm):
    module.init()
# Только оперативная память: обычная переписка не сохраняется в SQLite.
RECENT_MESSAGES = defaultdict(lambda: deque(maxlen=10))

# У каждого пользователя одно рабочее сообщение с кнопками.
# ID сохраняется в SQLite, поэтому перезапуск бота не создаёт новое окно.
with connect() as db:
    db.execute('''CREATE TABLE IF NOT EXISTS ui_messages (
        user_id INTEGER PRIMARY KEY,
        message_id TEXT NOT NULL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS old_ui_messages (
        user_id INTEGER NOT NULL,
        message_id TEXT NOT NULL,
        PRIMARY KEY (user_id, message_id)
    )''')

# Уведомления не становятся рабочим экраном до нажатия на их кнопку.
with connect() as db:
    db.execute("""CREATE TABLE IF NOT EXISTS ui_callbacks (
        user_id INTEGER NOT NULL, message_id TEXT NOT NULL, revision TEXT NOT NULL,
        PRIMARY KEY (user_id, message_id))""")

_ACTION_LOCKS = defaultdict(asyncio.Lock)
_CLEANUP_TASKS = {}


def serialized(handler):
    @wraps(handler)
    async def wrapped(event):
        async with _ACTION_LOCKS[event.message.sender.user_id]:
            try:
                remember_sender(event.message.sender)
                return await handler(event)
            except Exception as error:
                LOG.warning("handler_failed type=%s", type(error).__name__)
                await notify(event.message.sender.user_id,
                             'Не удалось обновить экран. Открой /start для продолжения.')
    return wrapped


async def api_call(operation, **kwargs):
    started = perf_counter()
    try:
        result = await getattr(bot, operation)(**kwargs)
        if result is None or getattr(result, 'success', True) is False:
            raise RuntimeError('MAX operation was not successful')
        return result
    finally:
        LOG.info("api operation=%s elapsed_ms=%.1f", operation,
                 (perf_counter() - started) * 1000)


def versioned_buttons(attachments):
    revision = uuid4().hex[:16]
    result = deepcopy(attachments or [])
    for attachment in result:
        if attachment.type == 'inline_keyboard':
            for row in attachment.payload.buttons:
                for button in row:
                    if button.type == 'callback':
                        button.payload = f'ui:{revision}:{button.payload or ""}'
                        if len(button.payload.encode('utf-8')) > 256:
                            raise ValueError('Callback payload is too long')
    return result, revision


def save_revision(uid, mid, revision):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO ui_callbacks VALUES (?, ?, ?)',
                   (uid, mid, revision))


def queue_cleanup(uid):
    task = _CLEANUP_TASKS.get(uid)
    if task is None or task.done():
        task = asyncio.create_task(remove_old_screens(uid))
        _CLEANUP_TASKS[uid] = task
        task.add_done_callback(lambda done: _CLEANUP_TASKS.pop(uid, None)
                               if _CLEANUP_TASKS.get(uid) is done else None)


_SCREEN_LOCKS = defaultdict(asyncio.Lock)


def screen_id(uid):
    with connect() as db:
        row = db.execute('SELECT message_id FROM ui_messages WHERE user_id=?', (uid,)).fetchone()
    return row['message_id'] if row else None


def remember_screen(uid, mid):
    if not mid:
        return
    with connect() as db:
        old = db.execute('SELECT message_id FROM ui_messages WHERE user_id=?',
                         (uid,)).fetchone()
        if old and old['message_id'] != mid:
            # Старое рабочее сообщение больше не нужно.
            db.execute('INSERT OR IGNORE INTO old_ui_messages VALUES (?, ?)',
                       (uid, old['message_id']))
        db.execute("""INSERT INTO ui_messages(user_id, message_id) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET message_id=excluded.message_id""",
            (uid, mid))
        db.execute('DELETE FROM old_ui_messages WHERE user_id=? AND message_id=?',
                   (uid, mid))


async def remove_old_screens(uid):
    """Старые экраны уже недоступны для callback; удаляем их вне перехода."""
    started = perf_counter()
    try:
        attempted = set()
        while True:
            with connect() as db:
                rows = db.execute('SELECT message_id FROM old_ui_messages WHERE user_id=?',
                                  (uid,)).fetchall()
            pending = [row for row in rows if row['message_id'] not in attempted]
            if not pending:
                break
            for row in pending:
                mid = row['message_id']
                attempted.add(mid)
                if mid == screen_id(uid):
                    continue
                try:
                    await api_call('delete_message', message_id=mid)
                except MaxApiError as error:
                    if error.code != 404:
                        LOG.warning('cleanup_failed type=%s', type(error).__name__)
                        return
                except Exception as error:
                    LOG.warning('cleanup_failed type=%s', type(error).__name__)
                    return
                with connect() as db:
                    db.execute('DELETE FROM old_ui_messages WHERE user_id=? AND message_id=?', (uid, mid))
                    db.execute('DELETE FROM ui_callbacks WHERE user_id=? AND message_id=?', (uid, mid))
                # Пауза относится только к фоновой очистке, не к ответу на кнопку.
                await asyncio.sleep(0.55)
    except Exception as error:
        LOG.warning('cleanup_failed type=%s', type(error).__name__)
    finally:
        LOG.info('cleanup elapsed_ms=%.1f', (perf_counter() - started) * 1000)


def sent_id(result):
    # maxapi возвращает SendedMessage с полем message.body.mid.
    message = getattr(result, 'message', None)
    body = getattr(message, 'body', None)
    return getattr(body, 'mid', None)


async def show_screen(uid, text, attachments=None, callback_message=None, force_new=False):
    async with _SCREEN_LOCKS[uid]:
        callback_mid = getattr(getattr(callback_message, 'body', None), 'mid', None)
        mid = callback_mid or screen_id(uid)
        buttons, revision = versioned_buttons(attachments)
        if mid and not force_new:
            try:
                result = await api_call('edit_message', message_id=mid, text=text[:3999],
                                        attachments=buttons, notify=False, format='markdown' if isinstance(text,Markdown) else None)
            except MaxApiError as error:
                # Только достоверное отсутствие сообщения разрешает замену.
                if error.code != 404:
                    raise
            else:
                remember_screen(uid, mid)
                save_revision(uid, mid, revision)
                queue_cleanup(uid)
                return result
        sent = await api_call('send_message', user_id=uid, text=text[:3999], attachments=buttons, format='markdown' if isinstance(text,Markdown) else None)
        new_mid = sent_id(sent)
        if not new_mid:
            raise RuntimeError('MAX response has no message ID')
        remember_screen(uid, new_mid)
        save_revision(uid, new_mid, revision)
        queue_cleanup(uid)
        return sent


async def new_screen(uid, text, attachments=None):
    return await show_screen(uid, text, attachments, force_new=True)


def keyboard(rows):
    builder = InlineKeyboardBuilder()
    for row in rows:
        if row:
            builder.row(*row)
    return [builder.as_markup()]


def b(text, payload):
    return B(text=text, payload=payload)


def ui(text, rows):
    return text[:4000], keyboard(rows)


def home_btn():
    return [b('🏠 В меню', 'home')]


def me(uid):
    return profiles.get(uid)


def name(uid):
    profile = me(uid)
    return profile['name'] if profile and profile['name'] else 'Участник'

def contact_name(uid):
    p=me(uid)
    return people.card_text(p) if p and p['role']=='teacher' and p['teacher_status']=='approved' else name(uid)


def ready(uid):
    p=me(uid)
    if p and p['role']=='teacher':
        return te.is_teacher(uid) and not social.banned(uid)
    s=people.settings(uid)
    return bool(s and s['visible'] and profiles.complete(uid) and not social.banned(uid))

def profile_button(uid):
    # Роль уже выбрана, но анкета может быть заполнена не до конца.
    return b('✏️ Изменить анкету' if profiles.complete(uid) else '✏️ Заполнить анкету', 'profile')


def menu(uid):
    p=me(uid)
    if p and p['role']=='teacher':
        rows=[[b('📅 Мероприятия','card:0')],[b('👥 Найти участников','p:student:0')],
              [b('➕ Добавить мероприятие','te:new')],[b('🗂 Мои мероприятия','te:mine')],
              [b('📩 Отправленные сообщения','dm:sent:0')]]
        if p['teacher_status']!='approved': rows.insert(0,[b('👩‍🏫 Подтвердить статус','te:check')])
    else:
        rows=[[b('🎯 Мероприятия','card:0'),b('⭐ Избранное','favorite:0')],
              [b('👥 Найти людей','p:student:0'),profile_button(uid)],
              [b('🤝 Друзья','friend:0')]]
        if social.pending(uid, True) or social.pending(uid, False):
            rows.insert(-1,[b('📩 Заявки','req:in:0')])
    if te.is_moderator(uid): rows.append([b('🛡 Модерация','mod:home')])
    return ui('🏠 Приветствуем тебя на Месте встречи! Ну что, какие планы?' if p and p['role']=='student' and not te.is_moderator(uid) else '🏠 Главное меню',rows)


def role_screen(uid):
    p=me(uid)
    if p:
        request = rr.pending_for_user(uid)
        role = 'педагог' if p['role']=='teacher' else 'школьник'
        if request:
            return ui(f'Твоя текущая роль: {role}.\n⏳ Заявка №{request["id"]} уже на рассмотрении модератора. До решения роль не меняется.', [[b('🏠 Назад','home')]])
        return ui(f'Ваша текущая роль: {role}. Чтобы подать заявку на смену роли, обратитесь к модератору.', [
            [b('✉ Написать модератору','role:select')],[b('◀ Назад','home')]])
    return ui('🎯 Место встречи изменить нельзя.\nА вот планы на выходные – очень даже!\nЯ помогу найти мероприятия, единомышленников и команду для твоей следующей большой идеи.\nРасскажи, кто ты, и начнём.',[
        [b('🎒 Я школьник','role:student'),b('👩‍🏫 Я педагог','role:teacher')]])


def profile_screen(uid):
    p=me(uid)
    if p and p['role']=='teacher':
        return teacher_screen(uid)
    if not p: return ui('🧩 Нужно собрать твою анкету\nПросто расскажи немного о себе – так я смогу искать людей с похожими интересами.',[
        [b('✏️ Заполнить анкету','role:select')],home_btn()])
    if not profiles.complete(uid):
        return ui('🧩 Нужно собрать твою анкету\nПросто расскажи немного о себе – так я смогу искать людей с похожими интересами.' if not te.is_moderator(uid) else '👤 Анкета ещё не заполнена. Добавь город, возраст, интересы и навыки.', [
            [b('✏️ Заполнить анкету','profile:edit')], home_btn()])
    visible=bool(people.settings(uid) and people.settings(uid)['visible'])
    text=people.card_text(p)+'\n\n'+('✅ Анкета опубликована.' if visible else '🔒 Анкета скрыта.')
    if p['role']=='teacher':text+='\n👩‍🏫 Проверка: '+p['teacher_status']
    return ui(text,[
        [b('🔒 Скрыть анкету','profile:hideconfirm') if visible else b('✅ Опубликовать','profile:publishconfirm')],
        [b('✏️ Изменить анкету','profile:edit'),b('🗑 Удалить','profile:deleteconfirm')],
        home_btn()])


def edit_menu():
    return ui('✏️ Что хочешь изменить?',[
        [b('📍 Город','profile:field:city'),b('🎂 Возраст','profile:field:age')],
        [b('💡 Интересы','profile:field:interests'),b('🛠 Навыки','profile:field:skills')],
        [b('◀ Назад', 'profile')]])


def tags_screen(uid,kind,group_index=-1):
    if kind not in ('interests','skills'):return profile_screen(uid)
    source=profiles.INTERESTS if kind=='interests' else profiles.SKILLS
    groups=list(source.items());picked=profiles.draft_selection(uid,kind)
    text=('🤔Выбери свои интересы. Всего можно отметить до 10 пунктов, поэтому выбирай только самые близкие!' if kind=='interests' else
          '💪Выбери свои навыки. Всего можно отметить до 10 пунктов, поэтому отмечай только самые прокачанные скиллы!')
    text+=' Сейчас у тебя выбраны: '+(', '.join(picked) or ('нет выбранных интересов' if kind=='interests' else 'нет выбранных навыков'))
    if me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid):
        text=('💡 Выбор интересов\nЧто тебе действительно нравится? Выбирай до 10 направлений. Увлекаться всем на свете не запрещено! (просто здесь нам не хватит кнопок)' if kind=='interests' else
              '🛠 Выбор навыков\nЧто у тебя получается особенно хорошо? Также до 10 скиллов. Скромность приветствуется, но можно и немного похвастаться!')
        text+='\nСейчас у тебя выбрано: '+(', '.join(picked) or 'пока ничего')
    if len(picked)>=10:text+='\n'+('Тебя интересует очень многое! Жаль, экран слишком маленький, и ещё больше выбрать не получится' if kind=='interests' else 'Ты столько всего знаешь! Больше уже на экран не поместится!')
    if group_index<0:
        rows=[[b(n,f'tags:{kind}:{i}')] for i,(n,_) in enumerate(groups)]
        done=f'tags:done:{kind}'
    elif group_index<len(groups):
        group,opts=groups[group_index];text+='\n'+group
        rows=[[b(('✓ ' if v in picked else '')+v,f'tags:toggle:{kind}:{group_index}:{i}')] for i,v in enumerate(opts)]
        done=f'tags:{kind}'
    else:return tags_screen(uid,kind)
    return ui(text,rows+([[b('✅ Готово',done)]] if picked else [])+[[b('✕ Отмена','profile')]])


def wizard_prompt(uid,field):
    profiles.set_stage(uid,'wizard_'+field)
    if field in ('interests','skills'):
        profiles.begin_tags(uid,field)
        return tags_screen(uid,field)
    return ui('📍В каком городе ты живешь?' if field=='city' else '🎂 Сколько тебе лет? Напиши число.',[[b('✕ Отмена','profile')]])


def event_card(index,uid,mode='card'):
    student_text=bool(me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid))
    if mode=='favorite':
        ids=fav.ids(uid)
        with connect() as db: found=[db.execute('SELECT * FROM events WHERE id=?',(i,)).fetchone() for i in ids]
        found=[x for x in found if x]
    else: found=flt.results(uid) if mode=='result' else flt.results_unfiltered()
    found=[e for e in found if e and e['status']!='hidden']
    if not found:
        if mode=='favorite':return ui('⭐ Здесь пока пусто. Сохраняй интересные мероприятия!',[[b('🎯 Найти мероприятия','card:0')],home_btn()])
        if mode=='result':return ui('🕵️ Пока совпадений нет. Но мы же не на контрольной – можно попробовать ещё раз!' if student_text else '🔎 Пока ничего не найдено. Попробуй другие темы, место или дату.',[
            [b('⚙ Фильтры','f:home'),b('✕ Сбросить','f:clear:all')],home_btn()])
        return ui('📭 Афиши пока молчат. Загляни чуть позже – здесь появятся новые события.' if student_text else '📅 Сейчас мероприятий нет. Загляни сюда позже!',[home_btn()])
    index=max(0,min(index,len(found)-1));e=found[index]
    if e['status']=='hidden':
        return ui('Это мероприятие больше недоступно. Давай посмотрим другие!',[[b('🎯 К мероприятиям','card:0')],home_btn()])
    text=event_text(e)
    nav=[]
    if index: nav.append(b('◀ Назад',f'{mode}:{index-1}'))
    if index<len(found)-1:nav.append(b('Далее ▶',f'{mode}:{index+1}'))
    rows=[[b('✓ В избранном' if fav.saved(uid,e['id']) else '♡ В избранное',f'favtoggle:{e["id"]}:{mode}:{index}'),LinkButton(text='↗ Подробнее',url=e['url'])],
          [b('🏠 В меню','home'),b('⚙ Фильтры','f:home')],nav]
    if mode=='result' and index==0:text=(f'🔎 Нашли для тебя {len(found)} событий: \n\n' if student_text else f'✅ Нашли мероприятия! Подходящих событий: {len(found)}\n\n')+text
    return ui(text,rows)


def people_screen(uid,role='student',index=0):
    role='student'
    current=me(uid)
    if current and current['role']=='teacher' and not te.is_teacher(uid):
        return teacher_screen(uid)
    if not ready(uid):
        if current and current['role']=='teacher':
            return teacher_screen(uid)
        return ui('👥 Давай найдём единомышленников! Сначала заполни и опубликуй свою анкету. Контакты другим пользователям не показываем.',[
            [profile_button(uid)],home_btn()])
    found=people.list_people(uid,role)
    if not found:return ui('🪐 Твои единомышленники пока вне зоны обнаружения. Попробуем другие фильтры?' if current and current['role']=='student' and not te.is_moderator(uid) else '👥 Пока никого не нашли. Попробуй изменить возраст, интересы или навыки.',[
        [b('⚙ Фильтры','p:filters')],home_btn()])
    index=max(0,min(index,len(found)-1));p=found[index]
    nav=[]
    if index:nav.append(b('◀ Назад',f'p:{role}:{index-1}'))
    if index<len(found)-1:nav.append(b('Далее ▶',f'p:{role}:{index+1}'))
    return ui(people.card_text(p,index,len(found))+'\n\n'+('Хотите связаться?' if me(uid) and me(uid)['role']=='teacher' else 'Хочешь отправить заявку в друзья?' if te.is_moderator(uid) else '👥 Один в поле воин, но с командой всё-таки веселее. Хочешь добавить пользователя в друзья?\n\n🤝 Хорошие знакомства иногда начинаются с одной кнопки.'),[
        [b('📩 Связаться',f'dm:write:{p["user_id"]}') if me(uid) and me(uid)['role']=='teacher' else b('📩 Отправить заявку',f'req:send:{p["user_id"]}:{role}:{index}')],
        [b('⚙ Фильтры','p:filters'),b('🏠 В меню','home')],
        nav])


AGE_RANGES=[('До 11 лет','3-11'),('12–15 лет','12-15'),('16–18 лет','16-18'),('19–25 лет','19-25'),('26+ лет','26-100')]


def people_filters(uid):
    def preview(field):
        vals=people.get_values(uid,field)
        return ', '.join(vals) if vals else 'Любые'
    return ui('⚙ Фильтры людей\n\n🎂 Возраст: '+preview('ages')+
              '\n💡 Интересы: '+preview('interests')+'\n🛠 Навыки: '+preview('skills')+
              '',[
        [b('🎂 Возраст','pf:begin:ages'),b('💡 Интересы','pf:begin:interests')],
        [b('🛠 Навыки','pf:begin:skills')],
        [b('🔎 Смотреть анкеты','p:student:0')],
        [b('✕ Сбросить','pf:reset')],home_btn()])


def person_filter_screen(uid,field,group=-1):
    from taxonomy import INTERESTS, SKILLS
    if field=='ages':
        opts=AGE_RANGES
        selected=people.get_values(uid,field,True)
        return ui('🎂 Выбери возрастные группы:',[
            [b(('✓ ' if value in selected else '')+label,f'pf:toggle:ages:{i}',)]
            for i,(label,value) in enumerate(opts)]+[[b('✅ Готово','pf:save:ages'),b('◀ Назад (без сохранения)','pf:cancel')]])
    source=INTERESTS if field=='interests' else SKILLS
    groups=list(source.items())
    if group<0:
        return ui('Выбери категорию '+('интересов' if field=='interests' else 'навыков')+'. Выбрано: '+', '.join(people.get_values(uid,field,True)),[
            [b(n,f'pf:group:{field}:{i}')] for i,(n,_) in enumerate(groups)]+
            ([[b('✅ Готово','pf:save:'+field)]] if people.get_values(uid,field,True) else [])+[[b('◀ Назад (без сохранения)','pf:cancel')]])
    if group>=len(groups):return person_filter_screen(uid,field)
    title,opts=groups[group];selected=people.get_values(uid,field,True)
    return ui(title+'\nВыбрано: '+', '.join(selected)+'\nМожно выбрать до 10 пунктов.',[
        [b(('✓ ' if value in selected else '')+value,f'pf:toggle:{field}:{group}:{i}')]
        for i,value in enumerate(opts)]+[[b('◀ К категориям',f'pf:group:{field}:-1'),b('✅ Готово',f'pf:group:{field}:-1')],
                                        [b('✕ Отменить изменения','pf:cancel')]])


def sent_screen(uid,index=0):
    found=dm.sent_to(uid)
    if not found:return ui('📩 Отправленных сообщений пока нет.',[home_btn()])
    index=max(0,min(index,len(found)-1));peer=found[index];last=dm.last(uid,peer['user_id'])
    nav=[]
    if index:nav.append(b('◀ Назад',f'dm:sent:{index-1}'))
    if index<len(found)-1:nav.append(b('Далее ▶',f'dm:sent:{index+1}'))
    return ui(people.card_text(peer,index,len(found)),[
        [b('📩 Связаться',f'dm:write:{peer["user_id"]}'),b('↩️ Последнее сообщение',f'dm:last:{peer["user_id"]}')],
        nav,home_btn()])


def request_tabs(uid):
    """Показываем только направления с активными заявками."""
    tabs=[]
    if social.pending(uid, True):
        tabs.append(b('📥 Входящие', 'req:in:0'))
    if social.pending(uid, False):
        tabs.append(b('📤 Отправленные', 'req:out:0'))
    return [tabs] if tabs else []


def req_screen(uid,which='in',index=0):
    found=social.pending(uid,which=='in')
    if not found:
        other='out' if which=='in' else 'in'
        if social.pending(uid,other=='in'):
            return req_screen(uid,other,0)
        return ui('📩 Активных заявок пока нет. Когда появятся новые, здесь будут доступны нужные разделы.',[home_btn()])
    index=max(0,min(index,len(found)-1));r=found[index];peer=r['sender'] if which=='in' else r['recipient']
    text=f'📩 Заявка {index+1}/{len(found)}\n👤 {r["name"] or "Участник"}\n📍 {r["city"]}\n🎂 {r["age"] or "?"}\n💡 {", ".join(json.loads(r["interests"] or "[]"))}'
    rows=[[b('✅ Принять',f'req:accept:{peer}'),b('✕ Отклонить',f'req:reject:{peer}')]] if which=='in' else [[b('✕ Отменить заявку',f'req:cancel:{peer}')]]
    nav=[]
    if index:nav.append(b('◀ Назад',f'req:{which}:{index-1}'))
    if index<len(found)-1:nav.append(b('Далее ▶',f'req:{which}:{index+1}'))
    if nav: rows.append(nav)
    rows+=request_tabs(uid)
    rows.append(home_btn())
    return ui(text,rows)


def friends_screen(uid,index=0):
    found=social.friend_list(uid)
    if not found:return ui('🤝 Тут пока тихо. Но любая крутая команда когда-то состояла из незнакомцев.' if me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid) else '🤝 Здесь пока никого нет. Найди единомышленников и отправь заявку!',[[b('👥 Найти участников','p:student:0')],home_btn()])
    index=max(0,min(index,len(found)-1));p=found[index]
    nav=[]
    if index:nav.append(b('◀ Назад',f'friend:{index-1}'))
    if index<len(found)-1:nav.append(b('Далее ▶',f'friend:{index+1}'))
    return ui(people.card_text(p,index,len(found)),[
        [b('💬 Написать',f'dm:write:{p["user_id"]}'),b('🚫 Удалить',f'friend:confirm:{p["user_id"]}')],nav,home_btn()])


def chat_screen(uid):
    peer=social.partner(uid)
    if not peer:return ui('💬 Сейчас нет активного разговора.',[[b('🤝 Друзья','friend:0')],home_btn()])
    return ui('💬 Общение с '+name(peer)+'\nПиши обычные текстовые сообщения — бот передаст их собеседнику. MAX ID не раскрывается.',[
        [b('✕ Завершить разговор','chat:endconfirm'),b('🚩 Пожаловаться','chat:report')],home_btn()])


def teacher_screen(uid):
    p=me(uid)
    if not p or p['role']!='teacher':return ui('Доступно только педагогам.',[home_btn()])
    status=p['teacher_status']
    if status=='approved':
        return ui(f'✅ Статус педагога подтверждён.\nФИО: {p["name"]}\nМесто работы: {p["teacher_workplace"]}\nДолжность: {p["teacher_position"]}', [home_btn()])
    if status=='pending':return ui('⏳ Ваша заявка на подтверждение статуса педагога получена. Модератор рассмотрит её в ближайшее время и после этого вам станет доступен весь функционал платформы',[[b('🎯 Мероприятия','card:0')],home_btn()])
    draft=te.verification_draft(uid)
    rows=[[b('✏️ ФИО','te:verify:full_name')],
          [b('✏️ Место работы','te:verify:workplace')],
          [b('✏️ Должность','te:verify:position')]]
    if all(draft[f] for f in te.TEACHER_FIELDS):
        rows.append([b('📄 Отправить документ','te:verify:doc')])
    else:
        first=next(f for f in te.TEACHER_FIELDS if not draft[f])
        rows.append([b('▶ Продолжить заполнение',f'te:verify:{first}')])
    rows.append(home_btn())
    text=('👩‍🏫 Верификация педагога\n\n'
          f'ФИО: {draft["full_name"] or "Не заполнено"}\n'
          f'Место работы: {draft["workplace"] or "Не заполнено"}\n'
          f'Должность: {draft["position"] or "Не заполнено"}\n\n'
          'Заполните данные, затем отправьте ссылку на документ. Заявку увидят модераторы.')
    if status=='rejected':text='✕ Предыдущая заявка отклонена. Данные можно исправить и отправить заново.\n\n'+text
    return ui(text,rows)


EVENT_LABELS = {
    'title':'Название', 'description':'Описание', 'date':'Дата',
    'place':'Место', 'format':'Формат', 'tags':'Тематика', 'url':'Ссылка'
}
EVENT_STEPS = list(te.FIELDS)


def event_data(uid):
    row=te.draft(uid)
    return json.loads(row['draft']) if row else {}


def event_card_text(uid):
    return event_text(event_data(uid))


def event_topic_screen(uid, edit=False, group=None):
    """Выбор тем кнопками, как в фильтрах мероприятий."""
    data = event_data(uid)
    selected = et.normalize(data.get('tags', []))
    mode = 'edit' if edit else 'new'
    done = b('✅ Готово', f'te:topicdone:{mode}')
    if group is None:
        buttons = [[b(title, f'te:topicgroup:{mode}:{key}')]
                   for key, (title, _) in et.GROUPS.items()]
        return ui('🎭 Какие темы подходят мероприятию?\n'
                  f'Выбрано: {", ".join(selected) or "Пока ничего"}\n'
                  'Выберите одну или несколько тем из категорий.',
                  buttons + [[done], [b('◀️ Назад', 'te:edit' if edit else 'te:draft')]])
    if group not in et.GROUPS:
        return event_topic_screen(uid, edit)
    title, names = et.GROUPS[group]
    buttons = [[b(('✓ ' if tag in selected else '') + tag,
                  f'te:topictoggle:{mode}:{group}:{i}')]
               for i, tag in enumerate(names)]
    return ui(f'{title}\nВыбрано: {", ".join(selected) or "Пока ничего"}',
              buttons + [[b('✅ Готово', f'te:topicgroups:{mode}')]])


def event_prompt(uid,field,edit=False):
    if field not in EVENT_STEPS: return draft_screen(uid)
    if field == 'tags':
        profiles.set_stage(uid, '')
        return event_topic_screen(uid, edit)
    if field == 'format':
        profiles.set_stage(uid, '')
        current = event_data(uid).get('format')
        return ui(te.QUESTIONS['format'] + '\nВыбрано: ' + (current or 'Не выбрано'), [
            [b('🏫 Офлайн',f'te:format:{"edit" if edit else "new"}:офлайн'),
             b('💻 Онлайн',f'te:format:{"edit" if edit else "new"}:онлайн')],
            [b('◀️ Назад','te:edit' if edit else 'te:draft')]])
    profiles.set_stage(uid,('eventedit_' if edit else 'event_')+field)
    return ui(te.QUESTIONS[field],[[b('◀️ Назад','te:edit' if edit else 'te:draft')]])


def draft_screen(uid,mode='auto'):
    if not te.draft(uid):return ui('Нет текущего черновика.',[[b('➕ Новое мероприятие','te:new')],home_btn()])
    data=event_data(uid)
    if mode=='edit':
        return ui(event_card_text(uid)+'\n\nЧто Вы бы хотели изменить?',[
            [b('Название','te:editfield:title'),b('Описание','te:editfield:description')],
            [b('Дату','te:editfield:date'),b('Место','te:editfield:place')],
            [b('Формат','te:editfield:format'),b('Тематику','te:editfield:tags')],
            [b('Ссылку','te:editfield:url')],
            [b('◀️ Назад','te:draft'),b('✅Готово','te:draft')]])
    missing=next((field for field in EVENT_STEPS if not data.get(field)), None)
    if missing and mode!='preview':
        return event_prompt(uid,missing)
    return ui('✅ Всё заполнено!\nПроверьте информацию о мероприятии.\n'
              'Если всё верно, можете отправить карточку на проверку.\n\n'+event_card_text(uid),[
                  [b('✅ Отправить на проверку','te:send')],
                  [b('✏️ Изменить','te:edit')],
                  [b('◀️ Назад','home')]])


def mod_screen(uid,which='home',page=0):
    if not te.is_moderator(uid):return ui('⛔ Нет доступа к модерации.',[home_btn()])
    if which=='home':return ui('🛡 Меню модератора',[
        [b('👩‍🏫 Проверка педагогов','mod:teachers')],[b('📅 Проверка мероприятий','mod:events')],
        [b('🚩 Жалобы','mod:reports'),b('🚫 Заблокированные','mod:blocks')],
        [b('🔄 Заявки на смену роли','mod:roles')],
        [b('📅 Опубликованные','mod:published')],
        [b('🚪 Выйти из модерации','mod:logout')]])
    if which=='roles':
        found=rr.pending()
        if not found:return ui('✅ Заявок на смену роли пока нет.',[[b('◀ Назад','mod:home')]])
        r=found[0]
        old='педагог' if r['from_role']=='teacher' else 'школьник'
        new='педагог' if r['to_role']=='teacher' else 'школьник'
        return ui(f'🔄 Заявка №{r["id"]}\nПользователь: {r["name"] or "Участник"}\nТекущая роль: {old}\nЗапрашивает: {new}\n\nПричина: {r["reason"]}', [
            [b('✅ Сменить роль',f'mod:roleok:{r["id"]}')],
            [b('✕ Отклонить заявку',f'mod:rolereject:{r["id"]}')],
            [b('◀ Назад','mod:home')]])
    if which=='teachers':
        found=te.pending_teachers()
        if not found:return ui('Больше заявок нет.',[[b('◀ Назад','mod:home')]])
        r=found[0]
        return ui(f'👩‍🏫 Заявка №{r["id"]}\nФИО: {r["full_name"] or "Не указано"}\n'
                  f'Место работы: {r["workplace"] or "Не указано"}\n'
                  f'Должность: {r["position"] or "Не указано"}\n'
                  f'Ссылка на документ: {r["doc_url"]}',[
            [b('✅ Подтвердить',f'mod:teacherok:{r["id"]}'),b('✕ Отклонить',f'mod:teacherreject:{r["id"]}')],
            [b('◀ Назад','mod:home')]])
    if which=='events':
        found=te.pending_events()
        if not found:return ui('Больше мероприятий на проверке нет.',[[b('◀ Назад','mod:home')]])
        r=found[0]
        return ui(f'📅 Заявка №{r["id"]}\n'+event_text(json.loads(r['draft'])),[
            [b('✅ Опубликовать',f'mod:eventok:{r["id"]}'),b('✕ Отклонить',f'mod:eventreject:{r["id"]}')],
            [b('◀ Назад','mod:home')]])
    if which=='published':
        with connect() as db: found=db.execute("SELECT id,title,status FROM events WHERE status IS NULL OR status IN ('published','hidden') ORDER BY id DESC LIMIT 16 OFFSET ?",(max(0,page)*15,)).fetchall()
        return ui('📅 Опубликованные и скрытые события:',
            [[b(('👁 Восстановить ' if r['status']=='hidden' else '🚫 Скрыть ')+str(r['title'])[:40],
                f"mod:{'restore' if r['status']=='hidden' else 'hideconfirm'}:{r['id']}")] for r in found[:15]]+[[b('◀ Предыдущие',f'mod:published:{page-1}')] if page else [], [b('Следующие ▶',f'mod:published:{page+1}')] if len(found)>15 else [], [b('◀ Назад','mod:home')]])
    if which=='reports':
        with connect() as db: found=db.execute("SELECT * FROM reports WHERE state='pending' ORDER BY id").fetchall()
        if not found:return ui('Жалоб на проверке нет.',[[b('◀ Назад','mod:home')]])
        r=found[0]
        return ui(f'🚩 Жалоба №{r["id"]}\nПричина: {r["reason"]}\nЗаявитель: {r["reporter"]}\nНарушитель: {r["reported"]}',[
            [b('🚫 Заблокировать',f'mod:banconfirm:{r["reported"]}:{r["id"]}'),b('✅ Закрыть жалобу',f'mod:close:{r["id"]}')],
            [b('◀ Назад','mod:home')]])
    if which=='blocks':
        with connect() as db:found=db.execute('SELECT * FROM global_blocks ORDER BY user_id').fetchall()
        return ui('🚫 Заблокированные:\n'+'\n'.join(str(x['user_id']) for x in found) if found else 'Заблокированных нет.',[
            [b('🔓 Разблокировать '+str(x['user_id']),f'mod:unban:{x["user_id"]}')] for x in found[:10]]+[[b('◀ Назад','mod:home')]])
    return mod_screen(uid,'home')


async def notify(uid, text, attachments=None):
    try:
        if text=='Не удалось обновить экран. Открой /start для продолжения.' and me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid):
            text='🔧 Кажется, один винтик решил взять выходной. Не удалось обновить экран. Напиши /start, чтобы продолжить.'
        buttons, revision = versioned_buttons(attachments)
        sent = await api_call('send_message', user_id=uid, text=text[:4000], attachments=buttons)
        mid = sent_id(sent)
        if mid and buttons:
            save_revision(uid, mid, revision)
    except Exception as error:
        LOG.warning('notification_failed type=%s', type(error).__name__)


_NOTIFY_TASKS = set()


def remember_sender(user):
    profile=me(user.user_id)
    if profile and profile['role']=='student':
        display=' '.join(filter(None, [getattr(user,'first_name',None),getattr(user,'last_name',None)]))
        if display: profiles.set_field(user.user_id,'name',display[:100])


def report_notice(reporter, reported, reason, notify_moderators=True):
    # Автор получает подтверждение текущим экраном; адресату — отдельное уведомление.
    queue_notify(reported, f'{name(reporter)} подал жалобу модератору. Причина: {reason}. '
                 'Общение с данным пользователем прекращено и будет недоступно в течение следующих 7 дней. '
                 'Вы удалены из списка его друзей', keyboard([home_btn()]))
    if notify_moderators:
        with connect() as db:
            moderators=db.execute('SELECT user_id FROM moderator_users').fetchall()
        for row in moderators:
            queue_notify(row['user_id'],'🚩 Поступила жалоба. Откройте список для рассмотрения.',
                         keyboard([[b('🛡 Жалобы','mod:reports')]]))


def queue_notify(uid, text, attachments=None):
    """Отправляем уведомления, не задерживая обновление кнопок."""
    task = asyncio.create_task(notify(uid, text, attachments))
    _NOTIFY_TASKS.add(task)
    task.add_done_callback(_NOTIFY_TASKS.discard)


async def open_bot(user, *, system_timestamp=None):
    """Вызывается под блокировкой пользователя: меню и приветствие не гоняются."""
    uid=user.user_id
    data=profiles.ui_data(uid)
    if system_timestamp is not None:
        if system_timestamp <= data.get('last_bot_started', -1):
            return
        # Запоминаем до отправки: после сетевого таймаута доставка могла состояться.
        data['last_bot_started']=system_timestamp
    recent=time()-data.get('last_open_at',0)<3
    data['last_open_at']=time()
    profiles.save_ui_data(uid,data)
    remember_sender(user)
    text,attachments=menu(uid) if me(uid) else role_screen(uid)
    await show_screen(uid,text,attachments,
                      force_new=system_timestamp is None and not recent)


@dp.bot_started()
async def bot_started(event):
    async with _ACTION_LOCKS[event.user.user_id]:
        try:
            await open_bot(event.user,system_timestamp=event.timestamp)
        except Exception as error:
            # Не отправляем повторное приветствие при неопределённом исходе API.
            LOG.warning('bot_start_failed type=%s',type(error).__name__)


@dp.message_created(Command('start'))
@serialized
async def start(event):
    await open_bot(event.message.sender)


@dp.message_created(Command('role'))
@serialized
async def role_cmd(event):
    uid = event.message.sender.user_id
    profiles.set_stage(uid, '')
    text, attachments = role_screen(uid)
    await show_screen(uid, text, attachments, force_new=True)


@dp.message_created(Command('catalog'))
@serialized
async def catalog_cmd(event):
    text,attachments=event_card(0,event.message.sender.user_id)
    await show_screen(event.message.sender.user_id, text=text,attachments=attachments,force_new=True)

@dp.message_created(Command('people'))
@serialized
async def people_cmd(event):
    text,attachments=people_screen(event.message.sender.user_id)
    await show_screen(event.message.sender.user_id, text=text,attachments=attachments,force_new=True)

@dp.message_created(Command('profile'))
@serialized
async def profile_cmd(event):
    text,attachments=profile_screen(event.message.sender.user_id)
    await show_screen(event.message.sender.user_id, text=text,attachments=attachments,force_new=True)

@dp.message_created(Command('filters'))
@serialized
async def filters_cmd(event):
    text,attachments=flt.screen(event.message.sender.user_id)
    await show_screen(event.message.sender.user_id, text=text,attachments=attachments,force_new=True)

@dp.message_created(Command('moderator'))
@serialized
async def moderator_cmd(event):
    uid = event.message.sender.user_id
    if te.is_moderator(uid):
        text, attachments = mod_screen(uid)
    elif not te.code_configured():
        text, attachments = ui('🛡 Код модератора ещё не настроен владельцем бота.', [home_btn()])
    else:
        profiles.set_stage(uid, 'moderator_code')
        text, attachments = ui('🛡 Вход в модерацию\n\nОтправь секретный код следующим сообщением.\n'
                               'Не пересылай код другим пользователям.',
                               [[b('✕ Отмена','mod:cancel_login')], home_btn()])
    await show_screen(event.message.sender.user_id, text=text, attachments=attachments,force_new=True)


@dp.message_created(Command('moderator_logout'))
@serialized
async def moderator_logout_cmd(event):
    uid = event.message.sender.user_id
    te.logout_moderator(uid)
    profiles.set_stage(uid, '')
    text, attachments = menu(uid)
    await show_screen(event.message.sender.user_id, text='🚪 Вы вышли из режима модератора.\n\n' + text,
                               attachments=attachments,force_new=True)

@dp.message_callback()
async def callback(event):
    started = perf_counter()
    uid = event.callback.user.user_id
    LOG.info('callback received')
    try:
        # Сначала подтверждение, затем SQLite, ожидание блокировки и бизнес-логика.
        ack_started = perf_counter()
        try:
            await asyncio.wait_for(event.answer(), timeout=3)
        except Exception as error:
            LOG.warning('callback_ack_failed type=%s', type(error).__name__)
        finally:
            LOG.info('callback_ack elapsed_ms=%.1f', (perf_counter() - ack_started) * 1000)
        async with _ACTION_LOCKS[uid]:
            mid = getattr(getattr(event.message, 'body', None), 'mid', None)
            payload = event.callback.payload or ''
            prefix, separator, action = payload.partition(':')
            revision, _, action = action.partition(':')
            with connect() as db:
                row = db.execute('SELECT revision FROM ui_callbacks WHERE user_id=? AND message_id=?',
                                 (uid, mid)).fetchone()
                old = db.execute('SELECT 1 FROM old_ui_messages WHERE user_id=? AND message_id=?',
                                 (uid, mid)).fetchone()
                if prefix != 'ui' or not separator or not row or old or row['revision'] != revision:
                    LOG.info('callback ignored_stale')
                    return
                # Одно поколение кнопок применяем ровно один раз, даже при ошибке API.
                db.execute("UPDATE ui_callbacks SET revision='' WHERE user_id=? AND message_id=?", (uid, mid))
            remember_sender(event.callback.user)
            parts = action.split(':')
            stage = profiles.stage(uid)
            if stage.startswith('modteacher_') and parts[:2] in (['mod','teacherok'], ['mod','teacherreject']):
                return
            logic_started = perf_counter()
            try:
                text, attachments = await dispatch(uid, parts)
                remember_sender(event.callback.user)
            finally:
                LOG.info('callback_logic elapsed_ms=%.1f', (perf_counter() - logic_started) * 1000)
            await show_screen(uid, text, attachments, callback_message=event.message,
                              force_new=(stage != profiles.stage(uid) and (stage.startswith('wizard_') or profiles.stage(uid).startswith('wizard_'))))
    except Exception as error:
        LOG.warning('callback_failed type=%s', type(error).__name__)
        await notify(uid, 'Не удалось обновить экран. Открой /start для продолжения.')
    finally:
        LOG.info('callback_total elapsed_ms=%.1f', (perf_counter() - started) * 1000)


async def dispatch(uid,parts):
    act=parts[0]
    if social.banned(uid) and act not in ('home','card','favorite','result','f','role','profile'):
        return ui('Это действие недоступно: учётная запись заблокирована.',[home_btn()])
    if act=='mod' and len(parts)>1 and parts[1]=='logout':
        te.logout_moderator(uid)
        profiles.set_stage(uid,'')
        return ui('🚪 Доступ модератора отключён.',[home_btn()])
    if act=='mod' and len(parts)>1 and parts[1]=='cancel_login':
        profiles.set_stage(uid,'')
        return menu(uid)
    if act=='home':
        profiles.set_stage(uid,'')
        return menu(uid)
    if act=='role':
        if parts[1]=='change':return role_screen(uid)
        if parts[1]=='select':
            if me(uid):
                request=rr.pending_for_user(uid)
                if request:return role_screen(uid)
                target='педагог' if me(uid)['role']=='student' else 'школьник'
                profiles.set_stage(uid,'role_change_reason')
                return ui(f'🔄 Заявка на смену роли\nТы хочешь перейти в роль «{target}».\n\nОтправь причину смены роли (5–1000 символов). Решение примет модератор. ❗ После одобрения все данные прежней роли будут удалены. До решения сервис доступен в прежней роли.', [
                    [b('◀ Назад','role:cancel')]])
            return role_screen(uid)
        if parts[1]=='cancel':
            profiles.set_stage(uid,'')
            return role_screen(uid)
        if parts[1] in ('student','teacher'):
            if me(uid):return role_screen(uid)
            profiles.choose(uid,parts[1]);return menu(uid)
    if act in ('student','teacher'):
        if me(uid):return role_screen(uid)
        profiles.choose(uid,act);return menu(uid)
    if act=='profile':
        if me(uid) and me(uid)['role']=='teacher':
            return teacher_screen(uid)
        if len(parts)==1:
            profiles.set_stage(uid,'')
            return profile_screen(uid)
        action=parts[1]
        if action=='edit':
            return edit_menu() if profiles.complete(uid) else wizard_prompt(uid,'city')
        if action=='field':
            field=parts[2]
            if field in ('city','age'):
                profiles.set_stage(uid,field)
                return ui('📍В каком городе ты живешь?' if field=='city' else '🎂 Сколько тебе лет? Напиши число.',[
                    [b('◀ Отмена','profile')]])
            if field in ('interests','skills'):
                profiles.begin_tags(uid,field)
                return tags_screen(uid,field)
        if action=='publishconfirm':
            if not profiles.complete(uid):return ui('Сначала заполни город, возраст, интересы и навыки.',[
                [b('✏️ Заполнить анкету','profile:edit')],home_btn()])
            return ui('🌍 Опубликовать анкету? Другие участники смогут видеть твои данные из анкеты. Контакты останутся скрытыми.',[
                [b('✅ Да, опубликовать','profile:publish'),b('◀ Назад','profile')]])
        if action=='publish':
            ok=people.publish(uid)
            return ui(('✅ Анкета опубликована\nТеперь у тебя есть шанс найти единомышленников!' if not te.is_moderator(uid) else '✅ Анкета опубликована!') if ok else 'Сначала заполни анкету.',[
                [b('👥 Искать участников','p:student:0'),profile_button(uid)]])
        if action=='hideconfirm':return ui('🔒 Скрыть анкету? Новые заявки станут недоступны; друзья сохранятся.',[
            [b('✅ Скрыть','profile:hide'),b('◀ Назад','profile')]])
        if action=='hide':
            people.hide(uid);return ui('🔒Анкета скрыта\nМесто встречи тайны не выдаёт. Но ты всё ещё можешь общаться с друзьями и просматривать мероприятия.' if not te.is_moderator(uid) else '🔒 Анкета скрыта.',[[profile_button(uid)],home_btn()])
        if action=='deleteconfirm':return ui('🗑 Удалить анкету? Это действие необратимо.',[
            [b('🗑 Да, удалить','profile:delete'),b('◀ Назад','profile')]])
        if action=='delete':
            people.delete(uid);return ui('✅ Анкета удалена.',[[b('✏️ Заполнить анкету','profile:edit')],home_btn()])
    if act=='tags':
        if parts[1]=='done':
            kind=parts[2]
            if kind not in ('interests','skills') or not profiles.apply_tags(uid,kind):return tags_screen(uid,kind)
            if profiles.stage(uid)=='wizard_interests':return wizard_prompt(uid,'skills')
            profiles.set_stage(uid,'')
            if profiles.complete(uid):profiles.set_field(uid,'waiting',0)
            return profile_screen(uid)
        if parts[1]=='toggle':
            kind=parts[2];idx=int(parts[3]);choice=int(parts[4]);groups=list((profiles.INTERESTS if kind=='interests' else profiles.SKILLS).values())
            if 0<=idx<len(groups) and 0<=choice<len(groups[idx]):profiles.toggle_draft(uid,kind,groups[idx][choice])
            return tags_screen(uid,kind,idx)
        return tags_screen(uid,parts[1],int(parts[2]) if len(parts)>2 else -1)
    if act in ('card','favorite','result'):
        return event_card(int(parts[-1]),uid,act)
    if act=='favtoggle':
        fav.toggle(uid,int(parts[1]));return event_card(int(parts[3]),uid,parts[2])
    if act=='f':
        if len(parts)>2 and parts[1]=='datepick' and parts[2]=='choose':
            profiles.set_stage(uid,'event_date_filter')
            return ui('📆 Введите дату в формате ДД.ММ.ГГ (например, 06.07.27)',[[b('◀ Назад','f:date')]])
        if parts[1]=='results':return event_card(0,uid,'result')
        return flt.handle(uid,parts)
    if act=='p':
        sub=parts[1]
        if sub in ('student','teacher'):return people_screen(uid,sub,int(parts[2]))
        if sub=='filters':return people_filters(uid)
        if sub=='city':
            cities=sorted({x['city'] for x in people.list_people(uid,'student') if x['city']})
            value='' if parts[2]=='any' else (cities[int(parts[2])] if int(parts[2])<len(cities) else '')
            people.update_filter(uid,'city',value);return people_filters(uid)
        if sub=='reset':
            people.reset_filter(uid);return people_filters(uid)
    if act=='pf':
        sub=parts[1]
        if sub=='reset':people.reset_filter(uid);return people_filters(uid)
        if sub=='cancel':return people_filters(uid)
        if sub=='begin':people.begin_filter(uid,parts[2]);return person_filter_screen(uid,parts[2])
        if sub=='save':
            if parts[2]!='ages' and not people.get_values(uid,parts[2],True):return person_filter_screen(uid,parts[2])
            people.commit_filter(uid,parts[2]);return people_filters(uid)
        if sub=='group':return person_filter_screen(uid,parts[2],int(parts[3]))
        if sub=='toggle':
            field=parts[2]
            if field=='ages':
                idx=int(parts[3]);people.toggle_filter(uid,field,AGE_RANGES[idx][1]);return person_filter_screen(uid,field)
            from taxonomy import INTERESTS, SKILLS
            group=int(parts[3]);idx=int(parts[4]);source=list((INTERESTS if field=='interests' else SKILLS).values())
            if 0<=group<len(source) and 0<=idx<len(source[group]):people.toggle_filter(uid,field,source[group][idx])
            return person_filter_screen(uid,field,group)
    if act=='dm':
        sub=parts[1]
        if sub=='sent':return sent_screen(uid,int(parts[2]))
        if sub=='last':
            peer=int(parts[2]);last=dm.last(uid,peer)
            if not last:return ui('Последнее сообщение отсутствует.',[[b('📩 Отправленные','dm:sent:0')],home_btn()])
            return ui('Написал '+name(uid)+'\n\n'+last['text'],[
                [b('📩 Связаться',f'dm:write:{peer}')],[b('📩 Отправленные','dm:sent:0')],home_btn()])
        if sub in ('write','reply'):
            peer=int(parts[2])
            if not dm.allowed(uid,peer):return ui('Общение с этим пользователем недоступно.',[home_btn()])
            profiles.set_stage(uid,'dm_'+str(peer))
            if sub=='reply':
                prior=dm.last(peer,uid)
                prompt='✏ Напиши ответ для '+contact_name(peer)+'\n\n'+(prior['text'][:800] if prior else '')
            else:prompt='💬 Отправь сообщение для '+contact_name(peer)+'.\nБот передаст его одним сообщением.'
            return ui(prompt,[[b('✕ Отмена','dm:cancel')]])
        if sub=='cancel':profiles.set_stage(uid,'');return menu(uid)
        if sub=='report':
            peer=int(parts[2]);last=dm.last(peer,uid)
            if not last:return ui('Это сообщение уже недоступно.',[home_btn()])
            return ui('🚩 Что произошло? Выбери причину.',[
                [b(r,f'dm:reason:{peer}:{i}')] for i,r in enumerate(['Неприемлемое сообщение','Оскорбления','Спам','Другое'])]+
                [[b('◀ Назад','home')]])
        if sub=='reason':
            peer=int(parts[2]);idx=int(parts[3])
            opts=['Неприемлемое сообщение','Оскорбления','Спам','Другое']
            if idx==3:
                profiles.set_stage(uid,'dmreport_'+str(peer))
                return ui('Напиши причину жалобы одним сообщением.',[[b('◀ Отмена','home')]])
            success=dm.report(uid,peer,opts[idx])
            if success: report_notice(uid,peer,opts[idx])
            return ui('🚩 Жалоба отправлена модератору. Общение с пользователем прекращено.' if success else 'Жалоба недоступна.',[home_btn()])
    if act=='req':
        sub=parts[1]
        if sub in ('in','out'):return req_screen(uid,sub,int(parts[2]))
        if sub=='send':
            target=int(parts[2]);msg=social.request(uid,target)
            if msg.startswith('📩 Заявка отправлена'):queue_notify(target,f'📩 {name(uid)} хочет добавить тебя в друзья!',keyboard([[b('📩 Посмотреть заявки','req:in:0')]]))
            return ui('📨 Первый шаг сделан. Теперь слово за другой стороной.' if msg.startswith('📩 Заявка отправлена') and me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid) else msg,[[b('👥 Продолжить поиск',f'p:{parts[3]}:{parts[4]}'),b('📤 Мои заявки','req:out:0')],home_btn()])
        if sub in ('accept','reject'):
            target=int(parts[2]);msg=social.decision(uid,target,sub=='accept')
            if msg.startswith('🤝'):queue_notify(target,f'🎉 Есть контакт! {name(uid)} добавил(-а) вас в друзья.' if me(target) and me(target)['role']=='student' and not te.is_moderator(target) else f'🎉 {name(uid)} принял твою заявку! Теперь вы в друзьях.',keyboard([[b('🤝 Друзья','friend:0')]]))
            elif msg.startswith('✕'):queue_notify(target,f'{name(uid)} отклонил(-а) твою заявку. Ничего страшного – впереди ещё много знакомств.' if me(target) and me(target)['role']=='student' and not te.is_moderator(target) else f'{name(uid)} не принял твою заявку в друзья',keyboard([home_btn(),[b('👥 Найти людей','p:student:0')]]))
            return ui(msg,[[b('📩 К заявкам','req:in:0'),b('🤝 Друзья','friend:0')],home_btn()])
        if sub=='cancel':
            ok=social.cancel(uid,int(parts[2]));return ui('✅ Заявка отменена.' if ok else 'Заявка уже обработана.',[[b('📤 Мои заявки','req:out:0')],home_btn()])
    if act=='friend':
        if len(parts)==2 and parts[1].isdigit():return friends_screen(uid,int(parts[1]))
        if parts[1]=='confirm':return ui('🚫 Удалить из друзей?',[[b('✅ Удалить',f'friend:delete:{parts[2]}'),b('◀ Назад','friend:0')]])
        if parts[1]=='delete':
            social.remove_friend(uid,int(parts[2]));return friends_screen(uid)
    if act=='chat':
        sub=parts[1]
        if sub=='screen':return chat_screen(uid)
        if sub=='start':
            peer=int(parts[2]);msg=social.start(uid,peer)
            if msg.startswith('💬 Общение началось'):queue_notify(peer,'💬 С тобой начали разговор!',keyboard([[b('✏ Ответить','chat:screen')]]))
            return chat_screen(uid) if msg.startswith('💬 Общение началось') else ui(msg,[[b('🤝 Друзья','friend:0')],home_btn()])
        if sub=='endconfirm':return ui('Закончить этот разговор?',[[b('✅ Завершить','chat:end'),b('◀ Продолжить','chat:screen')]])
        if sub=='end':
            peer=social.partner(uid);social.end(uid)
            if peer:queue_notify(peer,'💬 Собеседник завершил разговор.')
            return ui('✅ Разговор завершён.',[[b('🤝 Друзья','friend:0')],home_btn()])
        if sub=='report':
            return ui('🚩 Что произошло? Выбери причину.',[
                [b(x,f'chat:reason:{i}')] for i,x in enumerate(['Неприемлемые сообщения','Оскорбления','Спам','Другое'])]+[[b('◀ Назад','chat:screen')]])
        if sub=='reason':
            peer=social.partner(uid)
            if not peer:return ui('Разговор уже завершён.',[home_btn()])
            options=['Неприемлемые сообщения','Оскорбления','Спам','Другое'];reason=options[int(parts[2])]
            history='\n'.join(RECENT_MESSAGES.get(social.pair(uid,peer),[]))
            if not social.report(uid,peer,reason):return ui('Жалоба уже отправлена или недоступна.',[home_btn()])
            report_notice(uid,peer,reason,notify_moderators=False)
            with connect() as db:
                moderators=[r['user_id'] for r in db.execute('SELECT user_id FROM moderator_users')]
            for mid in moderators:
                queue_notify(mid,f'🚩 Жалоба: {reason}\nОт: {uid}, на: {peer}\nПоследние сообщения (только память):\n{history}'[:3900])
            RECENT_MESSAGES.pop(social.pair(uid,peer),None)
            return ui('🚩 Жалоба отправлена модератору. Общение прекращено.',[home_btn()])
    if act=='te':
        sub=parts[1]
        if sub=='check':return teacher_screen(uid)
        if sub=='checkstart':
            if not me(uid) or me(uid)['role']!='teacher':return teacher_screen(uid)
            draft=te.verification_draft(uid)
            field=next((f for f in te.TEACHER_FIELDS if not draft[f]), 'doc')
            return await dispatch(uid,['te','verify',field])
        if sub=='verify':
            if not me(uid) or me(uid)['role']!='teacher':return teacher_screen(uid)
            if me(uid)['teacher_status'] in ('pending','approved'):return teacher_screen(uid)
            field=parts[2]
            if field=='doc':
                draft=te.verification_draft(uid)
                if not all(draft[f] for f in te.TEACHER_FIELDS):return teacher_screen(uid)
                profiles.set_stage(uid,'teacher_doc')
                return ui('📄 Отправьте ссылку на документ (http:// или https://). Его увидят только модераторы.',
                          [[b('◀ Назад','te:check')]])
            if field not in te.TEACHER_FIELDS:return teacher_screen(uid)
            profiles.set_stage(uid,'teacher_'+field)
            prompts={'full_name':'✏️ Укажите ФИО полностью.',
                     'workplace':'✏️ Укажите место работы (название образовательной организации).',
                     'position':'✏️ Укажите свою должность.'}
            return ui(prompts[field],[[b('◀ Назад','te:check')]])
        if sub=='new':
            if not te.is_teacher(uid):return teacher_screen(uid)
            if not te.start_draft(uid):return ui('Сначала завершите текущий черновик.',[home_btn()])
            return draft_screen(uid)
        if sub in ('field','editfield'):
            if not te.is_teacher(uid):return teacher_screen(uid)
            return event_prompt(uid,parts[2],edit=sub=='editfield')
        if sub=='format':
            if not te.is_teacher(uid):return teacher_screen(uid)
            # Старые кнопки te:format:офлайн тоже продолжают работать.
            edit=len(parts)>3 and parts[2]=='edit'
            value=parts[3] if len(parts)>3 else parts[2]
            if value not in ('онлайн', 'офлайн'): return event_prompt(uid,'format',edit=edit)
            te.set_draft(uid,'format',value)
            profiles.set_stage(uid,'')
            if edit:return draft_screen(uid,'edit')
            data=event_data(uid)
            next_field=next((f for f in EVENT_STEPS if not data.get(f)),None)
            return event_prompt(uid,next_field) if next_field else draft_screen(uid,'preview')
        if sub=='topicgroups':
            if not te.is_teacher(uid): return teacher_screen(uid)
            return event_topic_screen(uid, parts[2]=='edit')
        if sub=='topicgroup':
            if not te.is_teacher(uid): return teacher_screen(uid)
            return event_topic_screen(uid, parts[2]=='edit', parts[3])
        if sub=='topictoggle':
            if not te.is_teacher(uid): return teacher_screen(uid)
            edit = parts[2]=='edit'
            group = parts[3]
            try: tag = et.GROUPS[group][1][int(parts[4])]
            except (KeyError, IndexError, ValueError): return event_topic_screen(uid, edit)
            selected = et.normalize(event_data(uid).get('tags', []))
            if tag in selected: selected.remove(tag)
            else: selected.append(tag)
            te.set_draft(uid, 'tags', selected)
            return event_topic_screen(uid, edit, group)
        if sub=='topicdone':
            if not te.is_teacher(uid): return teacher_screen(uid)
            edit = parts[2]=='edit'
            if not et.normalize(event_data(uid).get('tags', [])):
                return event_topic_screen(uid, edit)
            if edit: return draft_screen(uid, 'edit')
            next_field=next((f for f in EVENT_STEPS if not event_data(uid).get(f)),None)
            return event_prompt(uid,next_field) if next_field else draft_screen(uid,'preview')
        if sub=='edit':
            profiles.set_stage(uid,'')
            return draft_screen(uid,'edit')
        if sub=='draft':
            profiles.set_stage(uid,'')
            return draft_screen(uid,'preview' if all(event_data(uid).get(f) for f in EVENT_STEPS) else 'auto')
        if sub=='send':
            if not te.is_teacher(uid):return teacher_screen(uid)
            ok=te.send_review(uid)
            return ui('⏳ Мероприятие отправлено на проверку. Мы сообщим, когда оно появится в каталоге.' if ok else 'Заполни все поля, включая корректную ссылку.',[
                [b('🗂 Мои мероприятия','te:mine'),b('✏ К черновику','te:draft')],home_btn()])
        if sub=='mine':
            if not te.is_teacher(uid):return teacher_screen(uid)
            rows=te.my_events(uid)
            if not rows:return ui('🗂 Вы пока не добавили мероприятий.',[[b('➕ Добавить','te:new')],home_btn()])
            return ui('🗂 Мои мероприятия',[
                [b(f'#{r["id"]}: {te.status_label(r)}',f'te:view:{r["id"]}')] for r in rows[:20]]+[[b('➕ Добавить','te:new')],home_btn()])
        if sub=='view':
            id=int(parts[2]);found=[r for r in te.my_events(uid) if r['id']==id]
            if not found:return ui('Не найдено.',[[b('◀ Назад','te:mine')]])
            r=found[0]
            return ui(f'🗂 Событие №{id}\nСтатус: {te.status_label(r)}\n'+event_text(json.loads(r['draft']))+'\nЗамечания: '+escape(r['comment'][:150]),[
                [b('✏ Исправить',f'te:redo:{id}')] if r['status'] in ('rejected','published','draft') else [],
                [b('◀ Назад','te:mine')]])
        if sub=='redo':
            if not te.is_teacher(uid):return teacher_screen(uid)
            target=int(parts[2]);found=[r for r in te.my_events(uid) if r['id']==target]
            if not found:return menu(uid)
            r=found[0]
            if r['event_id']:
                if not te.start_draft(uid,r['event_id']):
                    return ui('Данное мероприятие ещё рассматривается модератором. Дождитесь решения.',[[b('◀ Назад','te:mine')]])
                return draft_screen(uid)
            if r['status']=='draft':return draft_screen(uid)
            if not te.resume_rejected(uid,target):return ui('Сначала завершите текущий черновик.',[home_btn()])
            return draft_screen(uid)
    if act=='mod':
        if not te.is_moderator(uid):return ui('🔒 За этой дверью служебная комната. Вернёмся к доступным разделам?' if me(uid) and me(uid)['role']=='student' else 'Нет доступа.',[home_btn()])
        sub=parts[1]
        if sub in ('home','teachers','events','reports','blocks','published','roles'):return mod_screen(uid,sub,int(parts[2]) if sub=='published' and len(parts)>2 else 0)
        if sub=='rolereject':
            profiles.set_stage(uid,'modrole_'+parts[2])
            return ui('✏ Напиши причину отклонения заявки на смену роли:',[[b('◀ Назад','mod:roles')]])
        if sub=='roleok':
            target, message=rr.decide(uid,int(parts[2]),True)
            if target and message.startswith('✅'):
                flt.invalidate_events()
                for pair_ids in list(RECENT_MESSAGES):
                    if target in pair_ids: RECENT_MESSAGES.pop(pair_ids, None)
            if target:queue_notify(target, message+'\nОткрой /start для продолжения.')
            return ui(message,[[b('🔄 Следующая заявка','mod:roles')],[b('◀ Меню модератора','mod:home')]])
        if sub=='hideconfirm':
            return ui('🚫 Скрыть мероприятие из каталога?',[[b('✅ Скрыть',f'mod:hide:{parts[2]}'),b('◀ Назад','mod:published')]])
        if sub in ('hide','restore'):
            ok=te.set_visibility(uid,int(parts[2]),sub=='hide')
            flt.invalidate_events()
            return ui(('✅ Мероприятие скрыто.' if sub=='hide' else '✅ Мероприятие восстановлено.') if ok else 'Мероприятие недоступно.',
                      [[b('📅 К мероприятиям','mod:published')]])
        if sub=='teacherreject':
            profiles.set_stage(uid,'modteacher_'+parts[2])
            return ui('✏ Отправь комментарий к причине отклонения одним сообщением.',[[b('◀ Назад','mod:teachers')]])
        if sub=='eventreject':
            profiles.set_stage(uid,'modevent_'+parts[2])
            return ui('✏ Отправь замечания к мероприятию одним сообщением.',[[b('◀ Назад','mod:events')]])
        if sub=='teacherok':
            target=te.decide_teacher(uid,int(parts[2]),True)
            if target:queue_notify(target,'✅ Статус педагога подтверждён!',keyboard([home_btn()]))
            return mod_screen(uid,'teachers')
        if sub=='eventok':
            target=te.decide_event(uid,int(parts[2]),True)
            if target:
                flt.invalidate_events()
                queue_notify(target,te.publication_notice(int(parts[2])),keyboard([[b('📅 Мероприятия','card:0')],[b('🗂 Мои мероприятия','te:mine')]]))
            else:return ui('Заявка уже обработана либо обнаружен конфликт ссылки или владельца. Проверьте заявку.',[[b('◀ Назад','mod:events')]])
            return mod_screen(uid,'events')
        if sub=='close':
            with connect() as db:db.execute("UPDATE reports SET state='closed' WHERE id=? AND state='pending'",(int(parts[2]),))
            return mod_screen(uid,'reports')
        if sub=='banconfirm':return ui('🚫 Заблокировать пользователя? Он потеряет доступ к заявкам и переписке.',[
            [b('✅ Заблокировать',f'mod:ban:{parts[2]}:{parts[3]}'),b('◀ Назад','mod:reports')]])
        if sub=='ban':
            social.block_globally(int(parts[2]))
            with connect() as db:db.execute("UPDATE reports SET state='closed' WHERE id=?",(int(parts[3]),))
            return mod_screen(uid,'reports')
        if sub=='unban':
            with connect() as db:db.execute('DELETE FROM global_blocks WHERE user_id=?',(int(parts[2]),))
            return mod_screen(uid,'blocks')
    return ui('⌛ Эту кнопку мы уже нажимали. Открой актуальное меню через /start.' if me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid) else 'Кнопка устарела. Вернись в меню.',[home_btn()])


@dp.message_created(F.message.body.text)
@serialized
async def text_handler(event):
    uid=event.message.sender.user_id
    remember_sender(event.message.sender)
    value=(event.message.body.text or '').strip()
    if not value or value.startswith('/'):return
    stage=profiles.stage(uid)
    if stage in ('wizard_city','wizard_age'):
        if stage=='wizard_city':
            if not 1<=len(value)<=70:return await new_screen(uid,'Укажи название города до 70 символов.')
            profiles.set_field(uid,'city',value)
            text,attachments=wizard_prompt(uid,'age')
        else:
            if not value.isdigit() or not 3<=int(value)<=100:return await new_screen(uid,'🙏Твой возраст прекрасен, но я его немного не понял. Введи целое число от 3 до 100.' if me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid) else 'Введи корректный возраст от 3 до 100.')
            profiles.set_field(uid,'age',int(value))
            text,attachments=wizard_prompt(uid,'interests')
        return await new_screen(uid,text,attachments)
    if stage == 'role_change_reason':
        if not 5 <= len(value) <= 1000:
            return await new_screen(event.message.sender.user_id, 'Напиши причину от 5 до 1000 символов.')
        ok, message = rr.submit(uid, value)
        if ok:
            profiles.set_stage(uid, '')
            # Уведомление не блокирует сохранение заявки и работу кнопок.
            with connect() as db:
                mods=[r['user_id'] for r in db.execute('SELECT user_id FROM moderator_users')]
            for mod in mods:
                queue_notify(mod,'🔄 Поступила заявка на смену роли.',keyboard([[b('🛡 Посмотреть заявки','mod:roles')]]))
        return await new_screen(event.message.sender.user_id, text=message,attachments=keyboard([[b('🏠 В меню','home')]]))
    if stage == 'event_date_filter':
        try:
            from datetime import datetime as dt
            import re
            if not re.fullmatch(r'\d{2}\.\d{2}\.\d{2}',value):raise ValueError()
            chosen=dt.strptime(value,'%d.%m.%y').date()
            flt.update(uid,'period',chosen.isoformat())
        except ValueError:
            return await new_screen(event.message.sender.user_id, '📅 Кажется, календарь нас не понял. Введи дату в формате ДД.ММ.ГГ.' if me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid) else 'Твоя дата не соответствует формату. Введи ДД.ММ.ГГ (например, 06.07.27).')
        profiles.set_stage(uid,'')
        text,attachments=flt.screen(uid, 'home')
        return await new_screen(event.message.sender.user_id, text=text,attachments=attachments)
    if stage.startswith('modrole_'):
        if not te.is_moderator(uid):return
        if not 3<=len(value)<=1000:return await new_screen(event.message.sender.user_id, 'Напиши причину от 3 до 1000 символов.')
        target,msg=rr.decide(uid,int(stage.split('_')[1]),False,value)
        profiles.set_stage(uid,'')
        if target:queue_notify(target,msg,keyboard([[b('✉ Написать модератору','role:change')],home_btn()]))
        text,attachments=mod_screen(uid,'roles')
        return await new_screen(event.message.sender.user_id, text=msg+'\n\n'+text,attachments=attachments)
    if stage.startswith('dmreport_'):
        peer=int(stage.split('_')[1]);profiles.set_stage(uid,'')
        if dm.report(uid,peer,'Другое: '+value[:800]):
            report_notice(uid,peer,'Другое: '+value[:800])
            return await new_screen(event.message.sender.user_id, '🚩 Жалоба отправлена. Общение прекращено.',attachments=keyboard([home_btn()]))
        return await new_screen(event.message.sender.user_id, 'Не удалось отправить жалобу.',attachments=keyboard([home_btn()]))
    if stage.startswith('dm_'):
        peer=int(stage.split('_')[1])
        ok,msg=dm.send(uid,peer,value)
        if ok:
            profiles.set_stage(uid,'')
            queue_notify(peer,('📩 Новое сообщение от педагога ' if me(uid)['role']=='teacher' else '💬 Новое сообщение от ')+contact_name(uid)+'!\n\n'+value[:3500],
                keyboard([[b('✏ Ответить',f'dm:reply:{uid}'),b('🚩 Пожаловаться',f'dm:report:{uid}')]]))
        return await new_screen(event.message.sender.user_id, text=msg,attachments=keyboard([[b('🤝 Друзья','friend:0')],home_btn()]))
    if stage == 'moderator_code':
        # Обработка отдельно от анкеты: код нельзя сохранить как поле профиля.
        ok, message = te.login_moderator(uid, value)
        if ok:
            profiles.set_stage(uid, '')
            text, attachments = mod_screen(uid)
            return await new_screen(event.message.sender.user_id, text=message+'\n\n'+text,attachments=attachments)
        if 'заблокирован' in message.lower() or 'не настроен' in message.lower():
            profiles.set_stage(uid, '')
        return await new_screen(event.message.sender.user_id, text=message, attachments=keyboard([
            [b('✕ Отмена','mod:cancel_login')],home_btn()]))
    if stage:
        p=me(uid)
        if stage=='city':
            if not 1<=len(value)<=70:return await new_screen(event.message.sender.user_id, 'Укажи название города до 70 символов.')
            profiles.set_field(uid,'city',value)
        elif stage=='age':
            if not value.isdigit() or not 3<=int(value)<=100:return await new_screen(event.message.sender.user_id, '🙏Твой возраст прекрасен, но я его немного не понял. Введи целое число от 3 до 100.' if p and p['role']=='student' and not te.is_moderator(uid) else 'Введи корректный возраст от 3 до 100.')
            profiles.set_field(uid,'age',int(value))
        elif stage.startswith('modteacher_'):
            if not te.is_moderator(uid):return
            target=te.decide_teacher(uid,int(stage.split('_')[1]),False,value[:1000])
            if target:await notify(target,'✕ Статус педагога не подтверждён. Причина: '+value[:1000])
        elif stage.startswith('modevent_'):
            if not te.is_moderator(uid):return
            proposal=te.get_proposal(int(stage.split('_')[1]))
            target=te.decide_event(uid,int(stage.split('_')[1]),False,value[:1000])
            if target:await notify(target,f'❌ Ваше мероприятие {json.loads(proposal["draft"]).get("title","")} было отклонено модератором. Можете ознакомиться с комментарием:\n{value[:1000]}'+('\nРанее опубликованная версия сохранена.' if proposal['event_id'] else ''),keyboard([[b('➕ Добавить мероприятие','te:new')],home_btn()]))
        elif stage.startswith('teacher_') and stage!='teacher_doc':
            field=stage[len('teacher_'):]
            if not te.save_verification_field(uid,field,value):
                return await new_screen(uid, 'Введите от 2 до 150 символов. ФИО указывайте минимум из двух слов.')
            profiles.set_stage(uid,'')
            next_fields=list(te.TEACHER_FIELDS)
            current=next_fields.index(field)
            if current+1<len(next_fields):
                text,attachments=await dispatch(uid,['te','verify',next_fields[current+1]])
            else:
                text,attachments=teacher_screen(uid)
            return await new_screen(uid,text='✅ Сохранено!\n\n'+text,attachments=attachments)
        elif stage=='teacher_doc':
            if not te.check_submit(uid,value):
                return await new_screen(uid, 'Проверьте, что ФИО, место работы и должность заполнены, а ссылка начинается с http:// или https://.')
        elif stage.startswith('event_') or stage.startswith('eventedit_'):
            edit=stage.startswith('eventedit_')
            field=stage[len('eventedit_'):] if edit else stage[len('event_'):]
            if field not in te.FIELDS:return
            if field in ('format','tags'):
                return await new_screen(uid, text='Выбери вариант кнопками.', attachments=event_prompt(uid, field, edit)[1])
            if field=='url' and not te.valid_url(value):
                return await new_screen(uid, 'Нужна ссылка, начинающаяся с http:// или https://.')
            if len(value)>2000:return await new_screen(uid, 'Максимум 2000 символов.')
            if field=='date':
                try:value=parse_teacher_date(value)
                except ValueError as error:return await new_screen(uid,str(error),event_prompt(uid,field,edit)[1])
            te.set_draft(uid,field,value)
        else:return
        profiles.set_stage(uid,'')
        if p and p['role']=='student' and profiles.complete(uid):profiles.set_field(uid,'waiting',0)
        if stage.startswith('eventedit_'):
            text,attachments=draft_screen(uid,'edit')
        elif stage.startswith('event_'):
            next_field=next((f for f in EVENT_STEPS if not event_data(uid).get(f)),None)
            text,attachments=event_prompt(uid,next_field) if next_field else draft_screen(uid,'preview')
        elif stage.startswith('modteacher_'):text,attachments=mod_screen(uid,'teachers')
        elif stage.startswith('modevent_'):text,attachments=mod_screen(uid,'events')
        elif stage=='teacher_doc':text,attachments=teacher_screen(uid)
        else:text,attachments=profile_screen(uid)
        return await new_screen(event.message.sender.user_id, text='✅ Изменения сохранены!\n\n'+text,attachments=attachments)
    peer=social.partner(uid)
    if peer and social.friends(uid,peer) and not social.blocked(uid,peer) and not social.banned(uid) and not social.banned(peer):
        if len(value)>3800:return await new_screen(event.message.sender.user_id, 'Сообщение слишком длинное (максимум 3800 символов).')
        RECENT_MESSAGES[social.pair(uid,peer)].append(f'{name(uid)}: {value[:300]}')
        await notify(peer,f'💬 Новое сообщение от {name(uid)}:\n{value}',keyboard([
            [b('✏ Ответить','chat:screen'),b('✕ Завершить','chat:endconfirm')]]))
        return await new_screen(event.message.sender.user_id, 'Твоё сообщение передано собеседнику. Ждём ответа!',attachments=keyboard([
            [b('✕ Завершить разговор','chat:endconfirm'),b('🚩 Пожаловаться','chat:report')]]))
    text,attachments=menu(uid)
    await new_screen(event.message.sender.user_id, text='🤔 Такой команды я не знаю. Похоже, нам нужен другой маршрут. Напиши /start.' if me(uid) and me(uid)['role']=='student' and not te.is_moderator(uid) else 'Выбери действие кнопкой.\n'+text,attachments=attachments)


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    asyncio.run(dp.start_polling(bot))
