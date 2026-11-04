"""Каталог людей, лайки, заявки и ссылки MAX. Данные хранятся в SQLite."""
from urllib.parse import urlparse
from catalog import connect
from maxapi.types import CallbackButton as B
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def kb(rows):
    builder = InlineKeyboardBuilder()
    for row in rows:
        if row:
            builder.row(*row)
    return [builder.as_markup()]


def init():
    with connect() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS people_settings (
                user_id INTEGER PRIMARY KEY,
                visible INTEGER NOT NULL DEFAULT 0,
                contact_link TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS people_likes (
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                PRIMARY KEY (from_id, to_id)
            );
            CREATE TABLE IF NOT EXISTS people_requests (
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (from_id, to_id)
            );
        ''')


def get_settings(user_id):
    with connect() as db:
        return db.execute('SELECT * FROM people_settings WHERE user_id=?', (user_id,)).fetchone()


def publish(user_id):
    with connect() as db:
        p = db.execute('SELECT bio,waiting FROM profiles WHERE user_id=?', (user_id,)).fetchone()
        if not p or p['waiting'] or not (p['bio'] or '').strip():
            return False
        db.execute('''INSERT INTO people_settings(user_id, visible) VALUES (?,1)
                      ON CONFLICT(user_id) DO UPDATE SET visible=1''', (user_id,))
        return True


def hide(user_id):
    with connect() as db:
        db.execute('UPDATE people_settings SET visible=0 WHERE user_id=?', (user_id,))


def valid_link(link):
    url = urlparse(link.strip())
    return url.scheme == 'https' and url.hostname in ('max.ru', 'www.max.ru') and bool(url.path.strip('/'))


def save_link(user_id, link):
    link = link.strip()
    if not valid_link(link):
        return False
    with connect() as db:
        db.execute('''INSERT INTO people_settings(user_id,contact_link) VALUES (?,?)
                      ON CONFLICT(user_id) DO UPDATE SET contact_link=excluded.contact_link''',
                   (user_id, link))
    return True


def list_people(viewer_id, role):
    with connect() as db:
        return db.execute('''SELECT p.user_id, p.role, p.bio FROM profiles p
             JOIN people_settings s ON s.user_id=p.user_id
             WHERE s.visible=1 AND p.waiting=0 AND TRIM(p.bio)!=''
               AND p.user_id!=? AND p.role=? ORDER BY p.user_id''',
             (viewer_id, role)).fetchall()


def menu():
    return '👥 Кого хочешь найти?', kb([
        [B(text='🎒 Школьники', payload='p:student:0'),
         B(text='👩‍🏫 Педагоги', payload='p:teacher:0')],
        [B(text='👤 Моя анкета', payload='my_profile')],
        [B(text='📩 Заявки', payload='p:requests')],
        [B(text='🏠 Главное меню', payload='home')],
    ])


def card(viewer_id, role, index=0):
    if role not in ('student', 'teacher'):
        return menu()
    found = list_people(viewer_id, role)
    if not found:
        return '🔎 Пока нет опубликованных анкет в этой категории.', kb([
            [B(text='◀ К выбору', payload='p:menu')],
            [B(text='🏠 Главное меню', payload='home')],
        ])
    index = max(0, min(index, len(found)-1))
    p = found[index]
    with connect() as db:
        liked = db.execute('SELECT 1 FROM people_likes WHERE from_id=? AND to_id=?',
                           (viewer_id, p['user_id'])).fetchone() is not None
    label = '🎒 Школьник' if role == 'student' else '👩‍🏫 Педагог'
    text = f"👤 Анкета {index+1}/{len(found)}\n{label}\n\nО себе:\n{p['bio']}"
    nav = []
    if index:
        nav.append(B(text='◀ Назад', payload=f'p:{role}:{index-1}'))
    if index < len(found)-1:
        nav.append(B(text='Далее ▶', payload=f'p:{role}:{index+1}'))
    return text[:4000], kb([
        [B(text='❤️ Уже нравится' if liked else '♡ Нравится',
           payload=f'p:like:{p["user_id"]}:{role}:{index}')],
        nav,
        [B(text='👥 Выбор категории', payload='p:menu'),
         B(text='📩 Заявки', payload='p:requests')],
        [B(text='🏠 Главное меню', payload='home')],
    ])


def request_like(sender, owner):
    if sender == owner:
        return False
    with connect() as db:
        visible = db.execute('''SELECT 1 FROM people_settings s
            JOIN profiles p ON p.user_id=s.user_id
            WHERE s.user_id=? AND s.visible=1 AND p.waiting=0 AND TRIM(p.bio)!='' ''',
            (owner,)).fetchone()
        if not visible:
            return False
        db.execute('INSERT OR IGNORE INTO people_likes(from_id,to_id) VALUES (?,?)', (sender, owner))
        existing = db.execute('SELECT status FROM people_requests WHERE from_id=? AND to_id=?',
                              (sender, owner)).fetchone()
        if existing:
            return False
        db.execute('INSERT INTO people_requests(from_id,to_id,status) VALUES (?, ?, ?)',
                   (sender, owner, 'pending'))
        return True


def pending(user_id):
    with connect() as db:
        return db.execute('''SELECT r.from_id,p.role,p.bio FROM people_requests r
            JOIN profiles p ON p.user_id=r.from_id
            WHERE r.to_id=? AND r.status='pending' ORDER BY r.from_id''',
            (user_id,)).fetchall()


def requests_screen(user_id):
    found = pending(user_id)
    if not found:
        with connect() as db:
            accepted = db.execute('''SELECT s.contact_link FROM people_requests r
                JOIN people_settings s ON s.user_id=r.to_id
                WHERE r.from_id=? AND r.status='accepted' AND s.contact_link!='' ''',
                (user_id,)).fetchall()
        links = '\n'.join(x['contact_link'] for x in accepted)
        message = ('💬 Одобренные контакты MAX:\n' + links) if links else '📩 Новых заявок пока нет.'
        return message[:4000], kb([
            [B(text='👥 Искать людей', payload='p:menu')],
            [B(text='🏠 Главное меню', payload='home')],
        ])
    person = found[0]
    role = 'Школьник' if person['role']=='student' else 'Педагог'
    return (f'📩 Заявок: {len(found)}\n\n{role} заинтересовался твоей анкетой.\n'
            f'О себе: {person["bio"][:500]}\n\n'
            'Разрешить передать ему твою ссылку MAX?'), kb([
        [B(text='✅ Разрешить', payload=f'p:accept:{person["from_id"]}'),
         B(text='✕ Отклонить', payload=f'p:reject:{person["from_id"]}')],
        [B(text='◀ Назад', payload='p:menu')],
    ])


def decide(owner, sender, accept):
    with connect() as db:
        request = db.execute('''SELECT status FROM people_requests
            WHERE from_id=? AND to_id=?''', (sender, owner)).fetchone()
        if not request or request['status'] != 'pending':
            return 'Эта заявка уже обработана.', None
        settings = db.execute('SELECT contact_link FROM people_settings WHERE user_id=?',
                              (owner,)).fetchone()
        if accept and (not settings or not settings['contact_link']):
            return 'Сначала добавь ссылку на MAX в разделе «Моя анкета». Заявка сохранена.', None
        db.execute('UPDATE people_requests SET status=? WHERE from_id=? AND to_id=?',
                   ('accepted' if accept else 'rejected', sender, owner))
        return ('✅ Контакт разрешён.' if accept else '✕ Заявка отклонена.'), (settings['contact_link'] if accept else None)
