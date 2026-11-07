"""Подтверждение педагогов и модерация предложенных мероприятий."""
import json
import os
import hashlib
import hmac
from datetime import timedelta
from datetime import datetime, timezone
from urllib.parse import urlparse
from catalog import connect
from event_tags import normalize, ALL_TAGS

FIELDS=['title','description','date','place','format','tags','url']
QUESTIONS={
 'title':'Как называется мероприятие?',
 'description':'Коротко расскажите, что будет происходить.',
 'date':'Когда пройдёт мероприятие? Укажите дату в формате ГГГГ-ММ-ДД и время при необходимости.',
 'place':'Где пройдёт главное мероприятие?',
 'format':'Это онлайн- или офлайн-мероприятие?',
 'tags':'Какие темы подходят мероприятию? Выберите темы из списка.',
 'url':'Пришлите ссылку, где можно узнать подробности.\nК примеру, это может быть информационный пост в соцсети, сайт мероприятия или документ положения.'}

def init():
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS teacher_checks (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, doc_url TEXT NOT NULL,
          status TEXT DEFAULT 'pending', comment TEXT DEFAULT '', created TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS proposed_events (
          id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL, event_id INTEGER,
          draft TEXT NOT NULL, status TEXT DEFAULT 'draft', comment TEXT DEFAULT '',
          created TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS moderator_users (user_id INTEGER PRIMARY KEY);
        ''')
        db.execute('''CREATE TABLE IF NOT EXISTS teacher_verification_drafts (
            user_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL DEFAULT '',
            workplace TEXT NOT NULL DEFAULT '', position TEXT NOT NULL DEFAULT ''
        )''')
        check_cols={r['name'] for r in db.execute('PRAGMA table_info(teacher_checks)')}
        for field in ('full_name','workplace','position'):
            if field not in check_cols:
                db.execute(f"ALTER TABLE teacher_checks ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        names={r['name'] for r in db.execute('PRAGMA table_info(proposed_events)')}
        if 'review_hash' not in names:
            db.execute("ALTER TABLE proposed_events ADD COLUMN review_hash TEXT DEFAULT ''")
        db.execute("""CREATE TABLE IF NOT EXISTS moderator_login_attempts (
            user_id INTEGER PRIMARY KEY,
            failures INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT
        )""")

# Код доступа должен быть задан администратором в окружении контейнера,
# а не храниться в исходниках или базе данных.
def code_configured():
    code = os.getenv('MODERATOR_ACCESS_CODE', '')
    return len(code) >= 12


def login_moderator(uid, supplied):
    """Возвращает (успешно, текст). После 5 ошибок — блокировка на 15 минут."""
    if not code_configured():
        return False, 'Код модератора не настроен. Укажите MODERATOR_ACCESS_CODE (не меньше 12 символов) при запуске Docker.'
    now = datetime.now(timezone.utc)
    with connect() as db:
        attempt = db.execute('SELECT failures, locked_until FROM moderator_login_attempts WHERE user_id=?', (uid,)).fetchone()
        if attempt and attempt['locked_until']:
            until = datetime.fromisoformat(attempt['locked_until'])
            if until > now:
                return False, f'Слишком много попыток. Повторите после {until.strftime("%H:%M UTC")}.'
        expected = hashlib.sha256(os.environ['MODERATOR_ACCESS_CODE'].encode('utf-8')).digest()
        actual = hashlib.sha256(supplied.encode('utf-8')).digest()
        if not hmac.compare_digest(actual, expected):
            failures = (attempt['failures'] if attempt and not attempt['locked_until'] else 0) + 1
            lock = (now + timedelta(minutes=15)).isoformat() if failures >= 5 else None
            db.execute('INSERT INTO moderator_login_attempts (user_id, failures, locked_until) '
                       'VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET '
                       'failures=excluded.failures, locked_until=excluded.locked_until', (uid, failures, lock))
            return False, ('Слишком много попыток. Вход временно заблокирован на 15 минут.'
                           if lock else f'Неверный код. Осталось попыток: {5-failures}.')
        db.execute('INSERT OR IGNORE INTO moderator_users(user_id) VALUES (?)', (uid,))
        db.execute('DELETE FROM moderator_login_attempts WHERE user_id=?', (uid,))
        return True, 'Доступ к модерации открыт.'


def logout_moderator(uid):
    with connect() as db:
        db.execute('DELETE FROM moderator_users WHERE user_id=?', (uid,))


def valid_url(url):
    u=urlparse(url)
    return u.scheme in ('http','https') and bool(u.netloc)

TEACHER_FIELDS = {'full_name': 'ФИО', 'workplace': 'Место работы', 'position': 'Должность'}


def verification_draft(uid):
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO teacher_verification_drafts(user_id) VALUES (?)', (uid,))
        return db.execute('SELECT * FROM teacher_verification_drafts WHERE user_id=?', (uid,)).fetchone()


def save_verification_field(uid, field, value):
    if field not in TEACHER_FIELDS:
        return False
    value = ' '.join(value.strip().split())
    if not 2 <= len(value) <= 150:
        return False
    if field == 'full_name' and len(value.split()) < 2:
        return False
    with connect() as db:
        profile = db.execute('SELECT role,teacher_status FROM profiles WHERE user_id=?', (uid,)).fetchone()
        if not profile or profile['role'] != 'teacher' or profile['teacher_status'] in ('pending', 'approved'):
            return False
        db.execute('INSERT OR IGNORE INTO teacher_verification_drafts(user_id) VALUES (?)', (uid,))
        db.execute(f'UPDATE teacher_verification_drafts SET {field}=? WHERE user_id=?', (value, uid))
    return True


def check_submit(uid, link):
    if not valid_url(link) or len(link) > 2000:
        return False
    with connect() as db:
        profile = db.execute('SELECT role,teacher_status FROM profiles WHERE user_id=?', (uid,)).fetchone()
        draft = db.execute('SELECT * FROM teacher_verification_drafts WHERE user_id=?', (uid,)).fetchone()
        if not profile or profile['role'] != 'teacher' or profile['teacher_status'] in ('pending', 'approved'):
            return False
        if not draft or not all(draft[f] for f in TEACHER_FIELDS):
            return False
        db.execute('''INSERT INTO teacher_checks(user_id,full_name,workplace,position,doc_url,created)
            VALUES (?,?,?,?,?,?)''', (uid, draft['full_name'], draft['workplace'], draft['position'],
                                     link, datetime.now(timezone.utc).isoformat()))
        db.execute("UPDATE profiles SET teacher_status='pending' WHERE user_id=? AND role='teacher'", (uid,))
    return True

def is_teacher(uid):
    with connect() as db:
        p=db.execute("SELECT 1 FROM profiles WHERE user_id=? AND role='teacher' AND teacher_status='approved'",(uid,)).fetchone()
        return p is not None

def is_moderator(uid):
    with connect() as db:
        return db.execute('SELECT 1 FROM moderator_users WHERE user_id=?',(uid,)).fetchone() is not None

def pending_teachers():
    with connect() as db:
        return db.execute("SELECT * FROM teacher_checks WHERE status='pending' ORDER BY id").fetchall()

def decide_teacher(mod,id,approve,comment=''):
    if not is_moderator(mod): return None
    with connect() as db:
        r=db.execute("SELECT * FROM teacher_checks WHERE id=? AND status='pending'",(id,)).fetchone()
        if not r: return None
        db.execute("UPDATE teacher_checks SET status=?, comment=?, doc_url='' WHERE id=?",
                   ('approved' if approve else 'rejected', comment, id))
        if approve:
            # Подтверждённая заявка — единственный источник имени педагога в каталоге.
            db.execute('''UPDATE profiles SET teacher_status='approved', name=?,
                teacher_workplace=?, teacher_position=?, waiting=0 WHERE user_id=? AND role='teacher' ''',
                (r['full_name'], r['workplace'], r['position'], r['user_id']))
            db.execute('''INSERT INTO people_settings(user_id,visible) VALUES (?,1)
                ON CONFLICT(user_id) DO UPDATE SET visible=1''', (r['user_id'],))
            db.execute('DELETE FROM teacher_verification_drafts WHERE user_id=?', (r['user_id'],))
        else:
            db.execute("UPDATE profiles SET teacher_status='rejected' WHERE user_id=? AND role='teacher'", (r['user_id'],))
        return r['user_id']

def draft(uid):
    with connect() as db:
        return db.execute("SELECT * FROM proposed_events WHERE teacher_id=? AND status='draft' ORDER BY id DESC LIMIT 1",(uid,)).fetchone()

def start_draft(uid,event_id=None):
    if not is_teacher(uid): return None
    with connect() as db:
        existing=draft(uid)
        if existing: return existing['id']
        if event_id and db.execute("SELECT 1 FROM proposed_events WHERE teacher_id=? AND event_id=? AND status='pending'",(uid,event_id)).fetchone():return None
        data={}
        if event_id:
            e=db.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
            if e: data={field: e[field] for field in FIELDS}
        c=db.execute('INSERT INTO proposed_events(teacher_id,event_id,draft,created) VALUES (?,?,?,?)',
                     (uid,event_id,json.dumps(data,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        return c.lastrowid

def set_draft(uid,field,value):
    if field not in FIELDS: return False
    r=draft(uid)
    if not r: return False
    data=json.loads(r['draft']);data[field]=value
    with connect() as db: db.execute('UPDATE proposed_events SET draft=? WHERE id=?',(json.dumps(data,ensure_ascii=False),r['id']))
    return True

def send_review(uid):
    r=draft(uid)
    if not r: return False
    data=json.loads(r['draft'])
    if any(not data.get(f) for f in FIELDS) or not valid_url(data['url']):return False
    if data.get('format') not in ('онлайн','офлайн') or not isinstance(data.get('tags'),list):return False
    if not data['tags'] or any(tag not in ALL_TAGS for tag in data['tags']):return False
    digest=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if r['review_hash']==digest:return False
    with connect() as db:
        if r['event_id'] and db.execute("SELECT 1 FROM proposed_events WHERE event_id=? AND status='pending'",(r['event_id'],)).fetchone():return False
        db.execute("UPDATE proposed_events SET status='pending',review_hash=? WHERE id=?",(digest,r['id']))
    return True

def pending_events():
    with connect() as db:return db.execute("SELECT * FROM proposed_events WHERE status='pending' ORDER BY id").fetchall()

def decide_event(mod,id,approve,comment=''):
    if not is_moderator(mod):return None
    with connect() as db:
        r=db.execute("SELECT * FROM proposed_events WHERE id=? AND status='pending'",(id,)).fetchone()
        if not r:return None
        data=json.loads(r['draft'])
        if approve:
            values=(data['url'],data['title'],data['description'],data['date'],data['place'],data['format'],
                json.dumps(normalize(data['tags']),ensure_ascii=False))
            if r['event_id']:
                db.execute('''UPDATE events SET url=?,title=?,description=?,date=?,place=?,format=?,tags=?,status='published'
                    WHERE id=?''',(*values,r['event_id']))
            else:
                db.execute('''INSERT INTO events(url,title,description,date,place,format,tags,status)
                    VALUES (?,?,?,?,?,?,?,'published') ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,description=excluded.description,date=excluded.date,
                    place=excluded.place,format=excluded.format,tags=excluded.tags,status='published' ''',values)
        if approve:
            linked=db.execute('SELECT id FROM events WHERE url=?',(data['url'],)).fetchone()
            if linked: db.execute('UPDATE proposed_events SET event_id=? WHERE id=?',(linked['id'],id))
        db.execute('UPDATE proposed_events SET status=?,comment=? WHERE id=?',('published' if approve else 'rejected',comment,id))
        return r['teacher_id']

def my_events(uid):
    with connect() as db:return db.execute('SELECT * FROM proposed_events WHERE teacher_id=? ORDER BY id DESC',(uid,)).fetchall()
