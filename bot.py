"""«Место встречи»: кнопочный интерфейс событий, анкет, дружбы и модерации."""
import asyncio
import json
import os
from collections import defaultdict, deque
from datetime import datetime
from maxapi import Bot, Dispatcher, F
from maxapi.types import Command, CallbackButton as B, LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from catalog import connect
import profiles, people, social, teacher_events as te, filters as flt, favorites as fav, role_requests as rr, direct_messages as dm

bot = Bot(os.environ['MAX_BOT_TOKEN'])
dp = Dispatcher()

# Подготавливаем таблицы один раз при запуске.
for module in (profiles, social, people, te, flt, fav, rr, dm):
    module.init()
# Только оперативная память: обычная переписка не сохраняется в SQLite.
RECENT_MESSAGES = defaultdict(lambda: deque(maxlen=10))


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

def ready(uid):
    s=people.settings(uid)
    return bool(s and s['visible'] and profiles.complete(uid) and not social.banned(uid))

def menu(uid):
    p=me(uid)
    if p and p['role']=='teacher':
        rows=[[b('📅 Мероприятия','card:0'),b('👥 Найти участников','p:student:0')],
              [b('➕ Добавить мероприятие','te:new'),b('🗂 Мои мероприятия','te:mine')],
              [b('📩 Отправленные сообщения','dm:sent:0')],
              [b('👤 Моя анкета','profile')]]
        if p['teacher_status']!='approved': rows.insert(0,[b('👩‍🏫 Подтвердить статус','te:check')])
    else:
        rows=[[b('🎯 Мероприятия','card:0'),b('⭐ Избранное','favorite:0')],
              [b('👥 Найти людей','p:student:0'),b('👤 Моя анкета','profile')],
              [b('📩 Заявки','req:in:0'),b('🤝 Друзья','friend:0')]]
    if te.is_moderator(uid): rows.append([b('🛡 Модерация','mod:home')])
    # Смену роли можно запросить через повторный /start.
    rows.append([b('🔄 Сменить роль','role:change')])
    return ui('🏠 Главное меню',rows)


def role_screen(uid):
    p=me(uid)
    if p:
        request = rr.pending_for_user(uid)
        role = 'педагог' if p['role']=='teacher' else 'школьник'
        if request:
            return ui(f'Твоя текущая роль: {role}.\n⏳ Заявка №{request["id"]} уже на рассмотрении модератора. До решения роль не меняется.', [[b('🏠 Назад','home')]])
        return ui(f'Ваша текущая роль: {role}. Чтобы подать заявку на смену роли, обратитесь к модератору.', [
            [b('✉ Написать модератору','role:select'),b('◀ Назад','home')]])
    return ui('👋 Привет! Это «Место встречи»!\nЗдесь можно находить интересные мероприятия, знакомиться с единомышленниками и собирать команды для общих идей.\nДля начала расскажи, кто ты.',[
        [b('🎒 Я школьник','role:student'),b('👩‍🏫 Я педагог','role:teacher')]])


def profile_screen(uid):
    p=me(uid)
    if not p: return ui('👤 Моя анкета\nТы ещё не рассказал о себе. Заполни анкету, чтобы находить единомышленников.',[
        [b('✏ Создать анкету','role:select')],home_btn()])
    visible=bool(people.settings(uid) and people.settings(uid)['visible'])
    text=people.card_text(p)+'\n\n'+('✅ Анкета опубликована.' if visible else '🔒 Анкета скрыта.')
    if p['role']=='teacher':text+='\n👩‍🏫 Проверка: '+p['teacher_status']
    return ui(text,[
        [b('🔒 Скрыть анкету','profile:hideconfirm') if visible else b('✅ Опубликовать','profile:publishconfirm')],
        [b('✏ Изменить','profile:edit'),b('🗑 Удалить','profile:deleteconfirm')],
        [b('🔗 Ссылка MAX','profile:link')],home_btn()])


def edit_menu():
    return ui('✏ Что хочешь изменить?',[
        [b('📍 Город','profile:field:city'),b('🎂 Возраст','profile:field:age')],
        [b('💡 Интересы','profile:field:interests'),b('🛠 Навыки','profile:field:skills')],
        [b('👤 Моя анкета','profile')]])


def tags_screen(uid,kind,group_index=-1):
    source=profiles.INTERESTS if kind=='interests' else profiles.SKILLS
    groups=list(source.items())
    if group_index<0:
        return ui('Выбери категорию. Всего можно отметить до 10 пунктов:',
                  [[b(n,f'tags:{kind}:{i}') ] for i,(n,_) in enumerate(groups)]+
                  [[b('✅ Готово',f'tags:done:{kind}')]])
    if group_index>=len(groups):return tags_screen(uid,kind)
    group,opts=groups[group_index];picked=profiles.selection(uid,kind)
    return ui(group+'\nОтметь нужные варианты:',[
        [b(('✓ ' if v in picked else '')+v,f'tags:toggle:{kind}:{group_index}:{i}')]
        for i,v in enumerate(opts)]+[[b('◀ К категориям',f'tags:{kind}'),b('✅ Готово',f'tags:done:{kind}')]])


def event_card(index,uid,mode='card'):
    if mode=='favorite':
        ids=fav.ids(uid)
        with connect() as db: found=[db.execute('SELECT * FROM events WHERE id=?',(i,)).fetchone() for i in ids]
        found=[x for x in found if x]
    else: found=flt.results(uid) if mode=='result' else flt.results_unfiltered()
    found=[e for e in found if e and e['status']!='hidden']
    if not found:
        if mode=='favorite':return ui('⭐ Здесь пока пусто. Сохраняй интересные мероприятия!',[[b('🎯 Найти мероприятия','card:0')],home_btn()])
        if mode=='result':return ui('🔎 Пока ничего не найдено. Попробуй другие темы, место или дату.',[
            [b('⚙ Фильтры','f:home'),b('✕ Сбросить','f:clear:all')],home_btn()])
        return ui('📅 Сейчас мероприятий нет. Загляни сюда позже!',[home_btn()])
    index=max(0,min(index,len(found)-1));e=found[index]
    if e['status']=='hidden':
        return ui('Это мероприятие больше недоступно. Давай посмотрим другие!',[[b('🎯 К мероприятиям','card:0')],home_btn()])
    date=e['date'] or 'Не указана'
    try:
        if date[:10].count('-')==2:
            parsed=datetime.strptime(date[:10],'%Y-%m-%d')
            months=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
            date=f'{parsed.day} {months[parsed.month-1]} {parsed.year}'+date[10:]
    except ValueError:pass
    tags=', '.join(json.loads(e['tags'] or '[]'))
    text=f'🎯 {e["title"]}\n📅 Когда: {date}\n📍 Где: {e["place"] or "Не указано"}\n🌐 Формат: {e["format"] or "Не указан"}\n\n{e["description"] or ""}\n\n{tags}'
    nav=[]
    if index: nav.append(b('◀ Назад',f'{mode}:{index-1}'))
    if index<len(found)-1:nav.append(b('Далее ▶',f'{mode}:{index+1}'))
    rows=[[b('✓ В избранном' if fav.saved(uid,e['id']) else '♡ В избранное',f'favtoggle:{e["id"]}:{mode}:{index}'),LinkButton(text='↗ Подробнее',url=e['url'])],
          [b('🏠 В меню','home'),b('⚙ Фильтры','f:home')],nav]
    if mode=='result' and index==0:text=f'✅ Нашли мероприятия! Подходящих событий: {len(found)}\n\n'+text
    return ui(text,rows)


def people_screen(uid,role='student',index=0):
    current=me(uid)
    if current and current['role']=='teacher' and not te.is_teacher(uid):
        return teacher_screen(uid)
    if not ready(uid):
        return ui('👥 Давай найдём единомышленников! Сначала заполни и опубликуй свою анкету. Контакты другим пользователям не показываем.',[
            [b('👤 Моя анкета','profile')],home_btn()])
    found=people.list_people(uid,role)
    if not found:return ui('👥 Пока никого не нашли. Попробуй изменить возраст, интересы или навыки.',[
        [b('⚙ Фильтры','p:filters'),b('👩‍🏫 Педагоги' if role=='student' else '🎒 Школьники',f'p:{"teacher" if role=="student" else "student"}:0')],home_btn()])
    index=max(0,min(index,len(found)-1));p=found[index]
    nav=[]
    if index:nav.append(b('◀ Назад',f'p:{role}:{index-1}'))
    if index<len(found)-1:nav.append(b('Далее ▶',f'p:{role}:{index+1}'))
    return ui(people.card_text(p,index,len(found))+'\n\n'+('Хотите связаться?' if me(uid) and me(uid)['role']=='teacher' else 'Хочешь отправить заявку в друзья?'),[
        [b('📩 Связаться',f'dm:write:{p["user_id"]}') if me(uid) and me(uid)['role']=='teacher' else b('📩 Отправить заявку',f'req:send:{p["user_id"]}:{role}:{index}')],
        [b('⚙ Фильтры','p:filters'),b('🏠 В меню','home')],
        [b('🎒 Школьники','p:student:0'),b('👩‍🏫 Педагоги','p:teacher:0')],nav])


AGE_RANGES=[('До 11 лет','3-11'),('12–15 лет','12-15'),('16–18 лет','16-18'),('19–25 лет','19-25'),('26+ лет','26-100')]


def people_filters(uid):
    def preview(field):
        vals=people.get_values(uid,field)
        return ', '.join(vals[:2])+(' +'+str(len(vals)-2) if len(vals)>2 else '') if vals else 'Любые'
    return ui('⚙ Фильтры людей\n\n🎂 Возраст: '+preview('ages')+
              '\n💡 Интересы: '+preview('interests')+'\n🛠 Навыки: '+preview('skills')+
              '\n\nГород на этапе MVP не ограничивает поиск.',[
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
        return ui('Выбери категорию '+('интересов' if field=='interests' else 'навыков')+':',[
            [b(n,f'pf:group:{field}:{i}')] for i,(n,_) in enumerate(groups)]+
            [[b('✅ Готово','pf:save:'+field),b('◀ Назад (без сохранения)','pf:cancel')]])
    if group>=len(groups):return person_filter_screen(uid,field)
    title,opts=groups[group];selected=people.get_values(uid,field,True)
    return ui(title+'\nМожно выбрать до 10 пунктов.',[
        [b(('✓ ' if value in selected else '')+value,f'pf:toggle:{field}:{group}:{i}')]
        for i,value in enumerate(opts)]+[[b('◀ К категориям',f'pf:group:{field}:-1'),b('✅ Готово','pf:save:'+field)],
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


def req_screen(uid,which='in',index=0):
    found=social.pending(uid,which=='in')
    if not found:return ui('📩 Новых заявок пока нет. Когда кто-нибудь захочет познакомиться, приглашение появится здесь.',[
        [b('📥 Входящие','req:in:0'),b('📤 Отправленные','req:out:0')],home_btn()])
    index=max(0,min(index,len(found)-1));r=found[index];peer=r['sender'] if which=='in' else r['recipient']
    text=f'📩 Заявка {index+1}/{len(found)}\n👤 {r["name"] or "Участник"}\n📍 {r["city"]}\n🎂 {r["age"] or "?"}\n💡 {", ".join(json.loads(r["interests"] or "[]"))}'
    rows=[[b('✅ Принять',f'req:accept:{peer}') ,b('✕ Отклонить',f'req:reject:{peer}')]] if which=='in' else [[b('✕ Отменить заявку',f'req:cancel:{peer}')]]
    nav=[]
    if index:nav.append(b('◀ Назад',f'req:{which}:{index-1}'))
    if index<len(found)-1:nav.append(b('Далее ▶',f'req:{which}:{index+1}'))
    return ui(text,rows+[nav,[b('📥 Входящие','req:in:0'),b('📤 Отправленные','req:out:0')],home_btn()])


def friends_screen(uid,index=0):
    found=social.friend_list(uid)
    if not found:return ui('🤝 Здесь пока никого нет. Найди единомышленников и отправь заявку!',[[b('👥 Найти участников','p:student:0')],home_btn()])
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
    if status=='approved':return ui('✅ Ваш статус педагога подтверждён.',[home_btn()])
    if status=='pending':return ui('⏳ Заявка получена! Модератор проверит документ.',[[b('🎯 Мероприятия','card:0')],home_btn()])
    return ui('👩‍🏫 Для публикации мероприятий нужно подтвердить работу в образовательной организации.\nПришли ссылку на документ — его увидят только модераторы.',[
        [b('✅ Пройти проверку','te:checkstart')],home_btn()])


def draft_screen(uid):
    r=te.draft(uid)
    if not r:return ui('Нет текущего черновика.',[[b('➕ Новый','te:new')],home_btn()])
    data=json.loads(r['draft'])
    text='➕ Новое мероприятие\n\n'+'\n'.join(f'{k}: {data.get(k,"—")}' for k in te.FIELDS)
    return ui(text,[
        [b('✏ '+field,f'te:field:{field}')] for field in te.FIELDS]+
        [[b('✅ Отправить на проверку','te:send')],home_btn()])


def mod_screen(uid,which='home'):
    if not te.is_moderator(uid):return ui('⛔ Нет доступа к модерации.',[home_btn()])
    if which=='home':return ui('🛡 Меню модератора',[
        [b('👩‍🏫 Проверка педагогов','mod:teachers'),b('📅 Проверка мероприятий','mod:events')],
        [b('🚩 Жалобы','mod:reports'),b('🚫 Заблокированные','mod:blocks')],
        [b('🔄 Заявки на смену роли','mod:roles')],
        [b('📅 Опубликованные','mod:published')],
        [b('🚪 Выйти из модерации','mod:logout')],home_btn()])
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
        return ui(f'👩‍🏫 Заявка №{r["id"]}\nСсылка: {r["doc_url"]}',[
            [b('✅ Подтвердить',f'mod:teacherok:{r["id"]}'),b('✕ Отклонить',f'mod:teacherreject:{r["id"]}')],
            [b('◀ Назад','mod:home')]])
    if which=='events':
        found=te.pending_events()
        if not found:return ui('Больше мероприятий на проверке нет.',[[b('◀ Назад','mod:home')]])
        r=found[0]
        return ui(f'📅 Заявка №{r["id"]}\n{r["draft"]}',[
            [b('✅ Опубликовать',f'mod:eventok:{r["id"]}'),b('✕ Отклонить',f'mod:eventreject:{r["id"]}')],
            [b('◀ Назад','mod:home')]])
    if which=='published':
        with connect() as db: found=db.execute("SELECT id,title FROM events WHERE status IS NULL OR status!='hidden' ORDER BY id DESC LIMIT 15").fetchall()
        return ui('📅 Опубликованные события (последние 15):',
            [[b(f'🚫 {str(r["title"])[:45]}',f'mod:hideconfirm:{r["id"]}')] for r in found]+[[b('◀ Назад','mod:home')]])
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


async def notify(uid,text,attachments=None):
    try:await bot.send_message(user_id=uid,text=text[:4000],attachments=attachments)
    except Exception:
        # Не останавливаем бота, если уведомление временно не доставлено.
        pass


_NOTIFY_TASKS = set()


def queue_notify(uid, text, attachments=None):
    """Отправляем уведомления, не задерживая обновление кнопок."""
    task = asyncio.create_task(notify(uid, text, attachments))
    _NOTIFY_TASKS.add(task)
    task.add_done_callback(_NOTIFY_TASKS.discard)


@dp.message_created(Command('start'))
async def start(event):
    uid=event.message.sender.user_id
    text,attachments=role_screen(uid)
    if social.partner(uid): text,attachments=ui('💬 У тебя есть активный разговор. Хочешь продолжить или завершить его?',[
        [b('💬 Продолжить','chat:screen'),b('✕ Завершить','chat:endconfirm')],home_btn()])
    await event.message.answer(text=text,attachments=attachments)

@dp.message_created(Command('catalog'))
async def catalog_cmd(event):
    text,attachments=event_card(0,event.message.sender.user_id)
    await event.message.answer(text=text,attachments=attachments)

@dp.message_created(Command('people'))
async def people_cmd(event):
    text,attachments=people_screen(event.message.sender.user_id)
    await event.message.answer(text=text,attachments=attachments)

@dp.message_created(Command('profile'))
async def profile_cmd(event):
    text,attachments=profile_screen(event.message.sender.user_id)
    await event.message.answer(text=text,attachments=attachments)

@dp.message_created(Command('filters'))
async def filters_cmd(event):
    text,attachments=flt.screen(event.message.sender.user_id)
    await event.message.answer(text=text,attachments=attachments)

@dp.message_created(Command('moderator'))
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
    await event.message.answer(text=text, attachments=attachments)


@dp.message_created(Command('moderator_logout'))
async def moderator_logout_cmd(event):
    uid = event.message.sender.user_id
    te.logout_moderator(uid)
    profiles.set_stage(uid, '')
    text, attachments = menu(uid)
    await event.message.answer(text='🚪 Вы вышли из режима модератора.\n\n' + text,
                               attachments=attachments)

@dp.message_callback()
async def callback(event):
    uid = event.callback.user.user_id
    parts = (event.callback.payload or '').split(':')

    # Подтверждаем нажатие и обновляем карточку одновременно.
    answer_task = asyncio.create_task(event.answer())
    try:
        text, attachments = await dispatch(uid, parts)
        await asyncio.gather(
            answer_task,
            event.message.edit(text=text[:4000], attachments=attachments),
        )
    except Exception:
        if not answer_task.done():
            answer_task.cancel()
        raise


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
        if len(parts)==1:
            profiles.set_stage(uid,'')
            return profile_screen(uid)
        action=parts[1]
        if action=='edit':return edit_menu()
        if action=='field':
            field=parts[2]
            if field in ('city','age'):
                profiles.set_stage(uid,field)
                return ui('📍 В каком городе ты живёшь?' if field=='city' else '🎂 Сколько тебе лет? Напиши число.',[
                    [b('◀ Отмена','profile')]])
            if field in ('interests','skills'):return tags_screen(uid,field)
        if action=='publishconfirm':
            if not profiles.complete(uid):return ui('Сначала заполни город, возраст, интересы и навыки.',[
                [b('✏ Изменить','profile:edit')],home_btn()])
            return ui('🌍 Опубликовать анкету? Другие участники смогут видеть твои данные из анкеты. Контакты останутся скрытыми.',[
                [b('✅ Да, опубликовать','profile:publish'),b('◀ Назад','profile')]])
        if action=='publish':
            ok=people.publish(uid)
            return ui('✅ Анкета опубликована!' if ok else 'Сначала заполни анкету.',[
                [b('👥 Искать участников','p:student:0'),b('👤 Моя анкета','profile')]])
        if action=='hideconfirm':return ui('🔒 Скрыть анкету? Новые заявки станут недоступны; друзья сохранятся.',[
            [b('✅ Скрыть','profile:hide'),b('◀ Назад','profile')]])
        if action=='hide':
            people.hide(uid);return ui('🔒 Анкета скрыта.',[[b('👤 Моя анкета','profile')],home_btn()])
        if action=='deleteconfirm':return ui('🗑 Удалить анкету? Это действие необратимо.',[
            [b('🗑 Да, удалить','profile:delete'),b('◀ Назад','profile')]])
        if action=='delete':
            people.delete(uid);return ui('✅ Анкета удалена.',[[b('✏ Заполнить заново','profile:edit')],home_btn()])
        if action=='link':
            profiles.set_stage(uid,'contact_link')
            return ui('🔗 Пришли ссылку https://max.ru/имя_профиля. Её увидят только участники, заявку которых ты примешь.',[
                [b('◀ Отмена','profile')]])
    if act=='tags':
        if parts[1]=='done':
            profiles.set_stage(uid,'')
            if profiles.complete(uid):profiles.set_field(uid,'waiting',0)
            return profile_screen(uid)
        if parts[1]=='toggle':
            kind=parts[2];idx=int(parts[3]);choice=int(parts[4]);groups=list((profiles.INTERESTS if kind=='interests' else profiles.SKILLS).values())
            if idx<len(groups) and choice<len(groups[idx]):profiles.toggle(uid,kind,groups[idx][choice])
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
            cities=sorted({x['city'] for x in people.list_people(uid,'student')+people.list_people(uid,'teacher') if x['city']})
            value='' if parts[2]=='any' else (cities[int(parts[2])] if int(parts[2])<len(cities) else '')
            people.update_filter(uid,'city',value);return people_filters(uid)
        if sub=='interests':return people_interest_screen()
        if sub=='interest':
            vals=[v for cat in profiles.INTERESTS.values() for v in cat]
            if int(parts[2])<len(vals):people.update_filter(uid,'interest',vals[int(parts[2])])
            return people_filters(uid)
        if sub=='reset':
            people.reset_filter(uid);return people_filters(uid)
    if act=='pf':
        sub=parts[1]
        if sub=='reset':people.reset_filter(uid);return people_filters(uid)
        if sub=='cancel':return people_filters(uid)
        if sub=='begin':people.begin_filter(uid,parts[2]);return person_filter_screen(uid,parts[2])
        if sub=='save':people.commit_filter(uid,parts[2]);return people_filters(uid)
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
                prompt='✏ Напиши ответ для '+name(peer)+'\n'+(prior['text'][:800] if prior else '')
            else:prompt='💬 Отправь сообщение для '+name(peer)+'.\nБот передаст его одним сообщением.'
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
            return ui('🚩 Жалоба отправлена модератору. Общение с пользователем прекращено.' if success else 'Жалоба недоступна.',[home_btn()])
    if act=='req':
        sub=parts[1]
        if sub in ('in','out'):return req_screen(uid,sub,int(parts[2]))
        if sub=='send':
            target=int(parts[2]);msg=social.request(uid,target)
            if msg.startswith('📩 Заявка отправлена'):queue_notify(target,f'📩 {name(uid)} хочет добавить тебя в друзья!',keyboard([[b('📩 Посмотреть заявки','req:in:0')]]))
            return ui(msg,[[b('👥 Продолжить поиск',f'p:{parts[3]}:{parts[4]}'),b('📤 Мои заявки','req:out:0')],home_btn()])
        if sub in ('accept','reject'):
            target=int(parts[2]);msg=social.decision(uid,target,sub=='accept')
            if msg.startswith('🤝'):queue_notify(target,f'🎉 {name(uid)} принял твою заявку! Теперь вы в друзьях.',keyboard([[b('🤝 Друзья','friend:0')]]))
            elif msg.startswith('✕'):queue_notify(target,'Твою заявку не приняли.')
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
            social.report(uid,peer,reason)
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
            profiles.set_stage(uid,'teacher_doc')
            return ui('Отправьте ссылку на документ, подтверждающий работу педагогом. Его увидят только модераторы.',[home_btn()])
        if sub=='new':
            if not te.is_teacher(uid):return teacher_screen(uid)
            te.start_draft(uid);return draft_screen(uid)
        if sub=='field':
            if not te.is_teacher(uid):return teacher_screen(uid)
            field=parts[2]
            if field=='format':return ui('Какой формат?',[
                [b('🏫 Офлайн','te:format:офлайн'),b('💻 Онлайн','te:format:онлайн')]])
            profiles.set_stage(uid,'event_'+field)
            return ui(te.QUESTIONS[field],[[b('◀ К черновику','te:draft')]])
        if sub=='format':te.set_draft(uid,'format',parts[2]);return draft_screen(uid)
        if sub=='draft':
            profiles.set_stage(uid,'')
            return draft_screen(uid)
        if sub=='send':
            if not te.is_teacher(uid):return teacher_screen(uid)
            ok=te.send_review(uid)
            return ui('⏳ Мероприятие отправлено на проверку.' if ok else 'Заполни все поля, включая корректную ссылку.',[
                [b('🗂 Мои мероприятия','te:mine'),b('✏ К черновику','te:draft')],home_btn()])
        if sub=='mine':
            if not te.is_teacher(uid):return teacher_screen(uid)
            rows=te.my_events(uid)
            if not rows:return ui('🗂 Вы пока не добавили мероприятий.',[[b('➕ Добавить','te:new')],home_btn()])
            return ui('🗂 Мои мероприятия',[
                [b(f'#{r["id"]}: {r["status"]}',f'te:view:{r["id"]}')] for r in rows[:20]]+[[b('➕ Добавить','te:new')],home_btn()])
        if sub=='view':
            id=int(parts[2]);found=[r for r in te.my_events(uid) if r['id']==id]
            if not found:return ui('Не найдено.',[[b('◀ Назад','te:mine')]])
            r=found[0]
            return ui(f'🗂 Событие №{id}\nСтатус: {r["status"]}\n{r["draft"]}\nЗамечания: {r["comment"]}',[
                [b('✏ Исправить',f'te:redo:{id}')] if r['status'] in ('rejected','published') else [],
                [b('◀ Назад','te:mine')]])
        if sub=='redo':
            if not te.is_teacher(uid):return teacher_screen(uid)
            target=int(parts[2]);found=[r for r in te.my_events(uid) if r['id']==target]
            if not found:return menu(uid)
            r=found[0]
            if r['status']=='published':
                if not r['event_id']: return ui('Не удалось найти опубликованную версию.',[[b('◀ Назад','te:mine')]])
                if not te.start_draft(uid,r['event_id']):
                    return ui('Данное мероприятие ещё рассматривается модератором. Дождитесь решения.',[[b('◀ Назад','te:mine')]])
                return draft_screen(uid)
            with connect() as db:
                db.execute("UPDATE proposed_events SET status='draft' WHERE teacher_id=? AND id=? AND status='rejected'",(uid,target))
            return draft_screen(uid)
    if act=='mod':
        if not te.is_moderator(uid):return ui('Нет доступа.',[home_btn()])
        sub=parts[1]
        if sub in ('home','teachers','events','reports','blocks','published','roles'):return mod_screen(uid,sub)
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
        if sub=='hide':
            with connect() as db:db.execute("UPDATE events SET status='hidden' WHERE id=?",(int(parts[2]),))
            flt.invalidate_events()
            return ui('✅ Мероприятие скрыто.',[[b('📅 К мероприятиям','mod:published')]])
        if sub=='teacherreject':
            profiles.set_stage(uid,'modteacher_'+parts[2])
            return ui('✏ Отправь комментарий к причине отклонения одним сообщением.',[[b('◀ Назад','mod:teachers')]])
        if sub=='eventreject':
            profiles.set_stage(uid,'modevent_'+parts[2])
            return ui('✏ Отправь замечания к мероприятию одним сообщением.',[[b('◀ Назад','mod:events')]])
        if sub=='teacherok':
            target=te.decide_teacher(uid,int(parts[2]),True)
            if target:queue_notify(target,'✅ Статус педагога подтверждён!')
            return mod_screen(uid,'teachers')
        if sub=='eventok':
            target=te.decide_event(uid,int(parts[2]),True)
            if target:
                flt.invalidate_events()
                queue_notify(target,'✅ Мероприятие опубликовано!')
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
    return ui('Кнопка устарела. Вернись в меню.',[home_btn()])


@dp.message_created(F.message.body.text)
async def text_handler(event):
    uid=event.message.sender.user_id
    value=(event.message.body.text or '').strip()
    if not value or value.startswith('/'):return
    stage=profiles.stage(uid)
    if stage == 'role_change_reason':
        if not 5 <= len(value) <= 1000:
            return await event.message.answer('Напиши причину от 5 до 1000 символов.')
        ok, message = rr.submit(uid, value)
        if ok:
            profiles.set_stage(uid, '')
            # Уведомление не блокирует сохранение заявки и работу кнопок.
            with connect() as db:
                mods=[r['user_id'] for r in db.execute('SELECT user_id FROM moderator_users')]
            for mod in mods:
                queue_notify(mod,'🔄 Поступила заявка на смену роли.',keyboard([[b('🛡 Посмотреть заявки','mod:roles')]]))
        return await event.message.answer(text=message,attachments=keyboard([[b('🏠 В меню','home')]]))
    if stage == 'event_date_filter':
        try:
            from datetime import datetime as dt
            import re
            if not re.fullmatch(r'\d{2}\.\d{2}\.\d{2}',value):raise ValueError()
            chosen=dt.strptime(value,'%d.%m.%y').date()
            flt.update(uid,'period',chosen.isoformat())
        except ValueError:
            return await event.message.answer('Твоя дата не соответствует формату. Введи ДД.ММ.ГГ (например, 06.07.27).')
        profiles.set_stage(uid,'')
        text,attachments=flt.screen(uid)
        return await event.message.answer(text='✅ Дата сохранена.\n'+text,attachments=attachments)
    if stage.startswith('modrole_'):
        if not te.is_moderator(uid):return
        if not 3<=len(value)<=1000:return await event.message.answer('Напиши причину от 3 до 1000 символов.')
        target,msg=rr.decide(uid,int(stage.split('_')[1]),False,value)
        profiles.set_stage(uid,'')
        if target:queue_notify(target,msg,keyboard([[b('✉ Написать модератору','role:change')],home_btn()]))
        text,attachments=mod_screen(uid,'roles')
        return await event.message.answer(text=msg+'\n\n'+text,attachments=attachments)
    if stage.startswith('dmreport_'):
        peer=int(stage.split('_')[1]);profiles.set_stage(uid,'')
        if dm.report(uid,peer,'Другое: '+value[:800]):
            return await event.message.answer('🚩 Жалоба отправлена. Общение прекращено.',attachments=keyboard([home_btn()]))
        return await event.message.answer('Не удалось отправить жалобу.',attachments=keyboard([home_btn()]))
    if stage.startswith('dm_'):
        peer=int(stage.split('_')[1])
        ok,msg=dm.send(uid,peer,value)
        if ok:
            profiles.set_stage(uid,'')
            queue_notify(peer,('📩 Новое сообщение от педагога ' if me(uid)['role']=='teacher' else '💬 Новое сообщение от ')+name(uid)+'!\n\n'+value[:3500],
                keyboard([[b('✏ Ответить',f'dm:reply:{uid}'),b('🚩 Пожаловаться',f'dm:report:{uid}')]]))
        return await event.message.answer(text=msg,attachments=keyboard([[b('🤝 Друзья','friend:0')],home_btn()]))
    if stage == 'moderator_code':
        # Обработка отдельно от анкеты: код нельзя сохранить как поле профиля.
        ok, message = te.login_moderator(uid, value)
        if ok:
            profiles.set_stage(uid, '')
            text, attachments = mod_screen(uid)
            return await event.message.answer(text=message+'\n\n'+text,attachments=attachments)
        if 'заблокирован' in message.lower() or 'не настроен' in message.lower():
            profiles.set_stage(uid, '')
        return await event.message.answer(text=message, attachments=keyboard([
            [b('✕ Отмена','mod:cancel_login')],home_btn()]))
    if stage:
        p=me(uid)
        if stage=='city':
            if not 1<=len(value)<=70:return await event.message.answer('Укажи название города до 70 символов.')
            profiles.set_field(uid,'city',value)
        elif stage=='age':
            if not value.isdigit() or not 3<=int(value)<=100:return await event.message.answer('Введи корректный возраст от 3 до 100.')
            profiles.set_field(uid,'age',int(value))
        elif stage=='contact_link':
            from urllib.parse import urlparse
            url=urlparse(value)
            if url.scheme!='https' or url.hostname not in ('max.ru','www.max.ru') or not url.path.strip('/'):
                return await event.message.answer('Нужна ссылка вида https://max.ru/имя_профиля')
            with connect() as db:
                db.execute('''INSERT INTO people_settings(user_id,contact_link) VALUES (?,?)
                    ON CONFLICT(user_id) DO UPDATE SET contact_link=excluded.contact_link''',(uid,value))
        elif stage.startswith('modteacher_'):
            if not te.is_moderator(uid):return
            target=te.decide_teacher(uid,int(stage.split('_')[1]),False,value[:1000])
            if target:await notify(target,'✕ Статус педагога не подтверждён. Причина: '+value[:1000])
        elif stage.startswith('modevent_'):
            if not te.is_moderator(uid):return
            target=te.decide_event(uid,int(stage.split('_')[1]),False,value[:1000])
            if target:await notify(target,'✕ Мероприятие отклонено. Замечания: '+value[:1000])
        elif stage=='teacher_doc':
            if not te.check_submit(uid,value):return await event.message.answer('Укажи корректную ссылку http/https.')
        elif stage.startswith('event_'):
            field=stage[6:]
            if field not in te.FIELDS:return
            if field=='url' and not te.valid_url(value):return await event.message.answer('Нужна ссылка http/https.')
            if len(value)>2000:return await event.message.answer('Максимум 2000 символов.')
            te.set_draft(uid,field,value)
        else:return
        profiles.set_stage(uid,'')
        if profiles.complete(uid):profiles.set_field(uid,'waiting',0)
        if stage.startswith('event_'):text,attachments=draft_screen(uid)
        elif stage.startswith('modteacher_'):text,attachments=mod_screen(uid,'teachers')
        elif stage.startswith('modevent_'):text,attachments=mod_screen(uid,'events')
        elif stage=='teacher_doc':text,attachments=teacher_screen(uid)
        else:text,attachments=profile_screen(uid)
        return await event.message.answer(text='✅ Изменения сохранены!\n\n'+text,attachments=attachments)
    peer=social.partner(uid)
    if peer and social.friends(uid,peer) and not social.blocked(uid,peer) and not social.banned(uid) and not social.banned(peer):
        if len(value)>3800:return await event.message.answer('Сообщение слишком длинное (максимум 3800 символов).')
        RECENT_MESSAGES[social.pair(uid,peer)].append(f'{name(uid)}: {value[:300]}')
        await notify(peer,f'💬 Новое сообщение от {name(uid)}:\n{value}',keyboard([
            [b('✏ Ответить','chat:screen'),b('✕ Завершить','chat:endconfirm')]]))
        return await event.message.answer('Твоё сообщение передано собеседнику. Ждём ответа!',attachments=keyboard([
            [b('✕ Завершить разговор','chat:endconfirm'),b('🚩 Пожаловаться','chat:report')]]))
    text,attachments=menu(uid)
    await event.message.answer(text='Выбери действие кнопкой.\n'+text,attachments=attachments)


if __name__=='__main__':
    asyncio.run(dp.start_polling(bot))
