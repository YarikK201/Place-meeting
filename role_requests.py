"""Заявки на смену роли. Применение решения атомарно очищает данные прежней роли."""
from datetime import datetime, timezone
from catalog import connect


def now():
    return datetime.now(timezone.utc).isoformat()


def init():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS role_change_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            from_role TEXT NOT NULL,
            to_role TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by INTEGER
        )''')
        names={r['name'] for r in db.execute('PRAGMA table_info(role_change_requests)')}
        if 'comment' not in names:db.execute("ALTER TABLE role_change_requests ADD COLUMN comment TEXT NOT NULL DEFAULT ''")
        db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_role_requests_one_pending
                      ON role_change_requests(user_id) WHERE status='pending' ''')


def pending_for_user(uid):
    with connect() as db:
        return db.execute("SELECT * FROM role_change_requests WHERE user_id=? AND status='pending'", (uid,)).fetchone()


def submit(uid, reason):
    reason = reason.strip()
    if len(reason) < 5 or len(reason) > 1000:
        return False, 'Напиши причину смены роли: от 5 до 1000 символов.'
    with connect() as db:
        p = db.execute('SELECT role FROM profiles WHERE user_id=?', (uid,)).fetchone()
        if not p:
            return False, 'Сначала выбери роль через /start.'
        if db.execute("SELECT 1 FROM role_change_requests WHERE user_id=? AND status='pending'", (uid,)).fetchone():
            return False, '⏳ Твоя заявка уже ожидает решения модератора.'
        target = 'teacher' if p['role'] == 'student' else 'student'
        db.execute('''INSERT INTO role_change_requests
            (user_id, from_role, to_role, reason, created_at)
            VALUES (?,?,?,?,?)''', (uid, p['role'], target, reason, now()))
    return True, '⏳ Заявка на смену роли отправлена модератору. До решения твоя текущая роль не изменится.'


def pending():
    with connect() as db:
        return db.execute('''SELECT r.*, p.name FROM role_change_requests r
            LEFT JOIN profiles p ON p.user_id=r.user_id
            WHERE r.status='pending' ORDER BY r.id''').fetchall()


def decide(mod_uid, request_id, approve, comment=''):
    """Возвращает (пользователь, текст). Повторное нажатие ничего не изменяет.

    Важно: при одобрении удаляются личная анкета, социальные связи и
    мероприятия, созданные пользователем как педагогом; общие события не затрагиваются.
    """
    with connect() as db:
        if not db.execute('SELECT 1 FROM moderator_users WHERE user_id=?', (mod_uid,)).fetchone():
            return None, '⛔ Нет доступа.'
        r = db.execute("SELECT * FROM role_change_requests WHERE id=? AND status='pending'", (request_id,)).fetchone()
        if not r:
            return None, 'Эта заявка уже обработана.'
        uid = r['user_id']
        p = db.execute('SELECT role FROM profiles WHERE user_id=?', (uid,)).fetchone()
        if not p or p['role'] != r['from_role']:
            db.execute("UPDATE role_change_requests SET status='cancelled',reviewed_at=?,reviewed_by=? WHERE id=?",
                       (now(), mod_uid, request_id))
            return uid, 'Заявка устарела: текущая роль изменилась.'
        if not approve:
            db.execute("UPDATE role_change_requests SET status='rejected',comment=?,reviewed_at=?,reviewed_by=? WHERE id=?",
                       (comment[:1000],now(), mod_uid, request_id))
            return uid, '✕ Заявка отклонена модератором. Причина: '+(comment[:1000] or 'не указана')+'\nРоль и данные сохранены.'

        # Сначала получить идентификаторы только авторских мероприятий.
        # Не затрагиваем события общего импортированного каталога.
        authored = [row[0] for row in db.execute(
            'SELECT DISTINCT event_id FROM proposed_events WHERE teacher_id=? AND event_id IS NOT NULL', (uid,))]
        for event_id in authored:
            db.execute('DELETE FROM favorites WHERE event_id=?', (event_id,))
            db.execute('DELETE FROM events WHERE id=?', (event_id,))
        db.execute('DELETE FROM proposed_events WHERE teacher_id=?', (uid,))
        db.execute("UPDATE teacher_checks SET status='cancelled' WHERE user_id=? AND status='pending'", (uid,))

        db.execute('DELETE FROM favorites WHERE user_id=?', (uid,))
        db.execute('DELETE FROM event_filters WHERE user_id=?', (uid,))
        db.execute('DELETE FROM people_filters WHERE user_id=?', (uid,))
        db.execute('DELETE FROM people_settings WHERE user_id=?', (uid,))
        db.execute('DELETE FROM people_likes WHERE from_id=? OR to_id=?', (uid, uid))
        db.execute('DELETE FROM people_requests WHERE from_id=? OR to_id=?', (uid, uid))
        db.execute('DELETE FROM friendship_requests WHERE sender=? OR recipient=?', (uid, uid))
        db.execute('DELETE FROM friendships WHERE uid1=? OR uid2=?', (uid, uid))
        db.execute('DELETE FROM conversations WHERE uid1=? OR uid2=?', (uid, uid))
        db.execute('DELETE FROM direct_messages WHERE sender=? OR recipient=?',(uid,uid))
        # Персональные блокировки и жалобы сохраняются ради безопасности.
        db.execute('''UPDATE profiles SET role=?, bio='', city='', age=NULL,
            interests='[]', skills='[]', waiting=1, teacher_status='unverified'
            WHERE user_id=?''', (r['to_role'], uid))
        db.execute('DELETE FROM user_workflows WHERE user_id=?', (uid,))
        db.execute("UPDATE role_change_requests SET status='approved',reviewed_at=?,reviewed_by=? WHERE id=?",
                   (now(), mod_uid, request_id))
        db.execute("UPDATE role_change_requests SET status='cancelled',reviewed_at=?,reviewed_by=? "
                   "WHERE user_id=? AND id!=? AND status='pending'", (now(), mod_uid, uid, request_id))
    return uid, '✅ Роль изменена. Данные и связи прежней роли очищены.'
