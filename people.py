"""Публикация профилей и кнопочные фильтры поиска по возрасту, интересам и навыкам."""
import json
from catalog import connect
import profiles
import social


def init():
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS people_settings (
            user_id INTEGER PRIMARY KEY, visible INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS people_likes(from_id INTEGER,to_id INTEGER,PRIMARY KEY(from_id,to_id));
            CREATE TABLE IF NOT EXISTS people_requests(from_id INTEGER,to_id INTEGER,status TEXT DEFAULT 'pending',PRIMARY KEY(from_id,to_id));
            CREATE TABLE IF NOT EXISTS people_filters(user_id INTEGER PRIMARY KEY,
              city TEXT DEFAULT '',interest TEXT DEFAULT '',role TEXT DEFAULT 'student');''')
        # У старых пользователей ссылка MAX могла быть записана в БД.
        # Стираем её при обновлении, больше эту информацию не собираем.
        old_cols={r['name'] for r in db.execute('PRAGMA table_info(people_settings)')}
        if 'contact_link' in old_cols:
            db.execute("UPDATE people_settings SET contact_link='' WHERE contact_link!=''")
        cols={r['name'] for r in db.execute('PRAGMA table_info(people_filters)')}
        for key in ('ages','interests','skills','draft_ages','draft_interests','draft_skills'):
            if key not in cols:
                db.execute(f"ALTER TABLE people_filters ADD COLUMN {key} TEXT NOT NULL DEFAULT '[]'")


def settings(uid):
    with connect() as db:
        return db.execute('SELECT * FROM people_settings WHERE user_id=?',(uid,)).fetchone()


def publish(uid):
    if not profiles.complete(uid): return False
    with connect() as db:
        db.execute('''INSERT INTO people_settings(user_id,visible) VALUES (?,1)
            ON CONFLICT(user_id) DO UPDATE SET visible=1''',(uid,))
        db.execute('UPDATE profiles SET waiting=0 WHERE user_id=?',(uid,))
    return True


def hide(uid):
    with connect() as db:
        db.execute('UPDATE people_settings SET visible=0 WHERE user_id=?',(uid,))
        db.execute("UPDATE friendship_requests SET status='cancelled' WHERE status='pending' AND (sender=? OR recipient=?)",(uid,uid))


def delete(uid):
    # Удаляем дружбу у обеих сторон и закрываем активные разговоры.
    # Всё происходит в одной транзакции, вместе с очисткой анкеты.
    with connect() as db:
        db.execute('DELETE FROM friendships WHERE uid1=? OR uid2=?', (uid, uid))
        db.execute('DELETE FROM conversations WHERE uid1=? OR uid2=?', (uid, uid))
        db.execute("""UPDATE friendship_requests SET status='cancelled'
            WHERE (sender=? OR recipient=?) AND status='pending'""", (uid, uid))
        db.execute('DELETE FROM people_settings WHERE user_id=?', (uid,))
        db.execute('DELETE FROM people_filters WHERE user_id=?', (uid,))
        db.execute('DELETE FROM direct_messages WHERE sender=? OR recipient=?', (uid, uid))
        db.execute("""UPDATE profiles SET bio='',city='',age=NULL,interests='[]',skills='[]',
            waiting=1 WHERE user_id=?""", (uid,))
        db.execute('DELETE FROM user_workflows WHERE user_id=?', (uid,))


def search_filter(uid):
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO people_filters(user_id) VALUES (?)',(uid,))
        return db.execute('SELECT * FROM people_filters WHERE user_id=?',(uid,)).fetchone()


def get_values(uid, field, draft=False):
    if field not in ('ages','interests','skills'): raise ValueError('invalid filter')
    row=search_filter(uid)
    return json.loads(row[('draft_' if draft else '')+field] or '[]')


def begin_filter(uid,field):
    current=get_values(uid,field)
    with connect() as db:
        db.execute(f'UPDATE people_filters SET draft_{field}=? WHERE user_id=?',(json.dumps(current,ensure_ascii=False),uid))


def toggle_filter(uid,field,item):
    selected=get_values(uid,field,True)
    if item in selected:selected.remove(item)
    elif len(selected)<10:selected.append(item)
    with connect() as db:
        db.execute(f'UPDATE people_filters SET draft_{field}=? WHERE user_id=?',(json.dumps(selected,ensure_ascii=False),uid))


def commit_filter(uid,field):
    with connect() as db:
        db.execute(f'UPDATE people_filters SET {field}=draft_{field} WHERE user_id=?',(uid,))


def reset_filter(uid):
    search_filter(uid)
    with connect() as db:
        db.execute("UPDATE people_filters SET ages='[]',interests='[]',skills='[]',city='',interest='' WHERE user_id=?",(uid,))


def update_filter(uid,field,value):
    if field not in ('city','interest','role'): return
    search_filter(uid)
    with connect() as db: db.execute(f'UPDATE people_filters SET {field}=? WHERE user_id=?',(value,uid))


def list_people(uid,role=None):
    s=settings(uid)
    owner=profiles.get(uid)
    if not owner or social.banned(uid):return []
    if owner['role']=='teacher':
        if owner['teacher_status']!='approved':return []
    elif not s or not s['visible'] or not profiles.complete(uid):
        return []
    f=search_filter(uid)
    role='student'
    with connect() as db:
        rows=db.execute('''SELECT p.* FROM profiles p JOIN people_settings s ON s.user_id=p.user_id
           WHERE s.visible=1 AND p.waiting=0 AND p.user_id!=? AND p.role=?
             AND NOT EXISTS(SELECT 1 FROM global_blocks g WHERE g.user_id=p.user_id)
             AND NOT EXISTS(SELECT 1 FROM interactions_blocked b WHERE
               ((b.blocker=p.user_id AND b.blocked=?) OR (b.blocker=? AND b.blocked=p.user_id)) AND (b.expires_at IS NULL OR b.expires_at>?))
             ORDER BY p.user_id''',(uid,role,uid,uid,social.UTC().isoformat())).fetchall()
    ages=get_values(uid,'ages');ints=get_values(uid,'interests');skills=get_values(uid,'skills')
    def matches(p):
        if ages and not (p['age'] is not None and any(lo<=p['age']<=hi for lo,hi in (map(int,a.split('-')) for a in ages))):return False
        pi=set(json.loads(p['interests'] or '[]'));ps=set(json.loads(p['skills'] or '[]'))
        return (not ints or bool(pi.intersection(ints))) and (not skills or bool(ps.intersection(skills)))
    def score(p):
        return (len(set(json.loads(p['interests'] or '[]')).intersection(ints))+
                len(set(json.loads(p['skills'] or '[]')).intersection(skills)))
    return sorted((p for p in rows if matches(p)),key=lambda p:(-score(p),p['user_id']))


def card_text(p,index=None,total=None):
    title=p['name'] or 'Участник'
    hdr=f'👤 {title}' if index is None else f'👤 {title} — {index+1}/{total}'
    if p['role']=='teacher':
        return (f'👩‍🏫 Педагог\n{hdr}\n🏫 Место работы: {p["teacher_workplace"] or "Не указано"}\n'
                f'💼 Должность: {p["teacher_position"] or "Не указана"}')
    def tags(k):return ', '.join(json.loads(p[k] or '[]')) or 'Не указано'
    return (f'{hdr}\n📍 Город: {p["city"] or "Не указан"}\n'
            f'🎂 Возраст: {p["age"] or "Не указан"}\n'
            f'💡 Интересы: {tags("interests")}\n🛠 Навыки: {tags("skills")}')
