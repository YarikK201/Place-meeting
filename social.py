"""Заявки, двусторонняя дружба, переписка, блокировки и жалобы."""
from datetime import datetime, timedelta, timezone
from catalog import connect

UTC=lambda: datetime.now(timezone.utc)

def init():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS friendship_requests (
          sender INTEGER NOT NULL, recipient INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', updated TEXT NOT NULL,
          PRIMARY KEY(sender,recipient));
        CREATE TABLE IF NOT EXISTS friendships (
          uid1 INTEGER NOT NULL, uid2 INTEGER NOT NULL, PRIMARY KEY(uid1,uid2));
        CREATE TABLE IF NOT EXISTS interactions_blocked (
          blocker INTEGER NOT NULL, blocked INTEGER NOT NULL, PRIMARY KEY(blocker,blocked));
        CREATE TABLE IF NOT EXISTS global_blocks (user_id INTEGER PRIMARY KEY,reason TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS conversations (
          uid1 INTEGER NOT NULL,uid2 INTEGER NOT NULL,started TEXT NOT NULL,
          PRIMARY KEY(uid1,uid2));
        CREATE TABLE IF NOT EXISTS reports (
          id INTEGER PRIMARY KEY, reporter INTEGER NOT NULL, reported INTEGER NOT NULL,
          reason TEXT NOT NULL, state TEXT DEFAULT 'pending', created TEXT NOT NULL);
        ''')

def pair(a,b): return (min(a,b),max(a,b))
def banned(uid):
    with connect() as db:
        return db.execute('SELECT 1 FROM global_blocks WHERE user_id=?',(uid,)).fetchone() is not None

def blocked(a,b):
    with connect() as db:
        return db.execute('SELECT 1 FROM interactions_blocked WHERE blocker IN (?,?) AND blocked IN (?,?) AND blocker!=blocked',(a,b,a,b)).fetchone() is not None

def friends(a,b):
    with connect() as db:
        return db.execute('SELECT 1 FROM friendships WHERE uid1=? AND uid2=?',pair(a,b)).fetchone() is not None

def request(sender, recipient):
    if sender==recipient or banned(sender) or banned(recipient) or blocked(sender,recipient): return '⛔ Заявка недоступна.'
    if friends(sender,recipient): return '🤝 Вы уже друзья.'
    with connect() as db:
        profile=db.execute('''SELECT 1 FROM profiles p JOIN people_settings s ON s.user_id=p.user_id
             WHERE p.user_id=? AND s.visible=1 AND p.waiting=0''',(recipient,)).fetchone()
        own=db.execute('''SELECT 1 FROM profiles p JOIN people_settings s ON s.user_id=p.user_id
             WHERE p.user_id=? AND s.visible=1 AND p.waiting=0''',(sender,)).fetchone()
        if not profile or not own: return 'Сначала опубликуй свою анкету; анкета получателя также должна быть открыта.'
        old=db.execute('SELECT * FROM friendship_requests WHERE sender=? AND recipient=?',(sender,recipient)).fetchone()
        if old:
            if old['status']=='pending': return '📩 Ты уже отправил заявку.'
            if old['status']=='rejected':
                allowed=datetime.fromisoformat(old['updated'])+timedelta(days=7)
                if UTC()<allowed: return '⏳ Повторная заявка будет доступна '+allowed.strftime('%d.%m.%Y')
        db.execute('''INSERT INTO friendship_requests(sender,recipient,status,updated) VALUES (?,?,?,?)
            ON CONFLICT(sender,recipient) DO UPDATE SET status=excluded.status,updated=excluded.updated''',
            (sender,recipient,'pending',UTC().isoformat()))
    return '📩 Заявка отправлена!'

def pending(uid, incoming=True):
    with connect() as db:
        return db.execute('''SELECT r.*,p.name,p.city,p.age,p.interests,p.skills
             FROM friendship_requests r JOIN profiles p ON p.user_id='''+('r.sender' if incoming else 'r.recipient')+
             ''' WHERE '''+('r.recipient' if incoming else 'r.sender')+'''=? AND r.status='pending' ORDER BY r.updated''',(uid,)).fetchall()

def decision(recipient,sender,approve):
    with connect() as db:
        old=db.execute('SELECT status FROM friendship_requests WHERE sender=? AND recipient=?',(sender,recipient)).fetchone()
        if not old or old['status']!='pending': return 'Эта заявка уже обработана.'
        if banned(sender) or banned(recipient) or blocked(sender,recipient): return 'Общение недоступно.'
        status='accepted' if approve else 'rejected'
        db.execute('UPDATE friendship_requests SET status=?,updated=? WHERE sender=? AND recipient=?',
                   (status,UTC().isoformat(),sender,recipient))
        if approve: db.execute('INSERT OR IGNORE INTO friendships(uid1,uid2) VALUES (?,?)',pair(sender,recipient))
    return '🤝 Заявка принята!' if approve else '✕ Заявка отклонена.'

def cancel(sender,recipient):
    with connect() as db:
        res=db.execute('''UPDATE friendship_requests SET status='cancelled',updated=?
                         WHERE sender=? AND recipient=? AND status='pending' ''',(UTC().isoformat(),sender,recipient))
        return bool(res.rowcount)

def friend_list(uid):
    with connect() as db:
        return db.execute('''SELECT p.* FROM friendships f JOIN profiles p
             ON p.user_id=CASE WHEN f.uid1=? THEN f.uid2 ELSE f.uid1 END
             WHERE f.uid1=? OR f.uid2=? ORDER BY p.name,p.user_id''',(uid,uid,uid)).fetchall()

def remove_friend(a,b):
    with connect() as db:
        db.execute('DELETE FROM friendships WHERE uid1=? AND uid2=?',pair(a,b))
        db.execute('DELETE FROM conversations WHERE uid1=? AND uid2=?',pair(a,b))

def active(uid):
    with connect() as db:
        return db.execute('SELECT * FROM conversations WHERE uid1=? OR uid2=?',(uid,uid)).fetchone()

def partner(uid):
    c=active(uid)
    return (c['uid2'] if c['uid1']==uid else c['uid1']) if c else None

def start(a,b):
    if not friends(a,b) or banned(a) or banned(b) or blocked(a,b): return 'Общение недоступно.'
    with connect() as db:
        busy=db.execute('SELECT 1 FROM conversations WHERE uid1 IN (?,?) OR uid2 IN (?,?)',(a,b,a,b)).fetchone()
        if busy: return '💬 Один из участников уже общается.'
        db.execute('INSERT INTO conversations VALUES (?,?,?)',(*pair(a,b),UTC().isoformat()))
    return '💬 Общение началось!'

def end(uid):
    with connect() as db:
        r=db.execute('DELETE FROM conversations WHERE uid1=? OR uid2=?',(uid,uid))
        return bool(r.rowcount)

def report(reporter,reported,reason):
    if not isinstance(reason,str) or not 1<=len(reason)<=1000: return False
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO interactions_blocked VALUES (?,?)',(reporter,reported))
        db.execute('DELETE FROM conversations WHERE uid1=? AND uid2=?',pair(reporter,reported))
        db.execute('DELETE FROM friendships WHERE uid1=? AND uid2=?',pair(reporter,reported))
        db.execute("UPDATE friendship_requests SET status='cancelled' WHERE status='pending' AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))",(reporter,reported,reported,reporter))
        db.execute('INSERT INTO reports(reporter,reported,reason,created) VALUES (?,?,?,?)',
                   (reporter,reported,reason,UTC().isoformat()))
    return True

def block_globally(uid,reason='Нарушение правил'):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO global_blocks VALUES (?,?)',(uid,reason))
        db.execute('DELETE FROM conversations WHERE uid1=? OR uid2=?',(uid,uid))
        db.execute("UPDATE friendship_requests SET status='cancelled' WHERE status='pending' AND (sender=? OR recipient=?)",(uid,uid))
