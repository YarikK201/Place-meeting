"""Одноразовые сообщения, ответы и история последнего сообщения для интерфейса.
Храним максимум одно последнее сообщение для каждой направленной пары пользователей.
"""
from datetime import datetime, timedelta, timezone
from catalog import connect
import social


def now():return datetime.now(timezone.utc).isoformat()


def init():
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS direct_messages(
          sender INTEGER NOT NULL, recipient INTEGER NOT NULL,
          text TEXT NOT NULL, sent_at TEXT NOT NULL,
          PRIMARY KEY(sender,recipient));
          CREATE INDEX IF NOT EXISTS idx_dm_recipient ON direct_messages(recipient);
        ''')
        db.execute('DELETE FROM direct_messages WHERE sent_at<?',((datetime.now(timezone.utc)-timedelta(days=30)).isoformat(),))


def allowed(sender,recipient):
    if sender==recipient or social.banned(sender) or social.banned(recipient) or social.blocked(sender,recipient):return False
    with connect() as db:
        rows=db.execute('''SELECT p.user_id,p.role,p.teacher_status,s.visible
            FROM profiles p LEFT JOIN people_settings s ON s.user_id=p.user_id
            WHERE p.user_id IN (?,?)''',(sender,recipient)).fetchall()
    if len(rows)!=2:return False
    roles={r['user_id']:r for r in rows}
    a,b=roles[sender],roles[recipient]
    # Ученик↔ученик: только взаимно принятая дружба; педагог↔ученик: подтверждённый педагог.
    if a['role']=='teacher' or b['role']=='teacher':
        teacher=a if a['role']=='teacher' else b
        return teacher['teacher_status']=='approved' and a['role']!=b['role'] and (bool(b['visible']) if sender==teacher['user_id'] else True)
    return social.friends(sender,recipient)


def send(sender,recipient,text):
    text=text.strip()
    if not text or len(text)>3500:return False,'Напиши сообщение от 1 до 3500 символов.'
    if not allowed(sender,recipient):return False,'Общение с этим пользователем сейчас недоступно.'
    with connect() as db:
        db.execute('''INSERT INTO direct_messages(sender,recipient,text,sent_at) VALUES (?,?,?,?)
            ON CONFLICT(sender,recipient) DO UPDATE SET text=excluded.text,sent_at=excluded.sent_at''',
            (sender,recipient,text,now()))
    return True,'✅ Сообщение отправлено.'


def last(sender,recipient):
    with connect() as db:
        return db.execute('SELECT * FROM direct_messages WHERE sender=? AND recipient=?',(sender,recipient)).fetchone()


def sent_to(uid):
    with connect() as db:
        return db.execute('''SELECT p.* FROM direct_messages m
            JOIN profiles p ON p.user_id=m.recipient
            WHERE m.sender=? ORDER BY m.sent_at DESC''',(uid,)).fetchall()


def clear_pair(a,b):
    with connect() as db:
        db.execute('DELETE FROM direct_messages WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)',(a,b,b,a))


def report(reporter,reported,reason):
    if reporter==reported:return False
    with connect() as db:
        recent=db.execute('SELECT 1 FROM direct_messages WHERE sender=? AND recipient=?',(reported,reporter)).fetchone()
        if not recent:return False
    social.report(reporter,reported,reason)
    clear_pair(reporter,reported)
    return True
