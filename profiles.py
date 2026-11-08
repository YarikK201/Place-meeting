"""Анкеты пользователей и пошаговое редактирование с миграцией старой схемы."""
import json
from catalog import connect

from taxonomy import INTERESTS, SKILLS


def init():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS profiles (
            user_id INTEGER PRIMARY KEY,role TEXT NOT NULL,bio TEXT DEFAULT '',waiting INTEGER DEFAULT 1
        )''')
        names = {r['name'] for r in db.execute('PRAGMA table_info(profiles)')}
        for name, typ in [('name','TEXT DEFAULT \'\''),('city','TEXT DEFAULT \'\''),
                          ('age','INTEGER'),('interests','TEXT DEFAULT \'[]\''),
                          ('skills','TEXT DEFAULT \'[]\''),('stage','TEXT DEFAULT \'\''),
                          ('teacher_status', "TEXT DEFAULT 'unverified'"),
                          ('teacher_workplace', "TEXT DEFAULT ''"),
                          ('teacher_position', "TEXT DEFAULT ''")]:
            if name not in names: db.execute(f'ALTER TABLE profiles ADD COLUMN {name} {typ}')
        db.execute('''CREATE TABLE IF NOT EXISTS user_workflows (
             user_id INTEGER PRIMARY KEY, stage TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}'
        )''')
        db.execute("UPDATE user_workflows SET stage='' WHERE stage='contact_link'")


def get(uid):
    with connect() as db:
        return db.execute('SELECT * FROM profiles WHERE user_id=?',(uid,)).fetchone()


def choose(uid, role, name=''):
    if role not in ('student','teacher'): return False
    with connect() as db:
        db.execute('''INSERT INTO profiles(user_id,role,name) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,
            name=CASE WHEN profiles.name='' THEN excluded.name ELSE profiles.name END''', (uid,role,name[:100]))
    return True


def set_field(uid, field, value):
    if field not in ('name','city','age','interests','skills','bio','teacher_status','waiting'): raise ValueError('invalid field')
    if field in ('interests','skills'): value=json.dumps(value,ensure_ascii=False)
    with connect() as db:
        db.execute(f'UPDATE profiles SET {field}=? WHERE user_id=?',(value,uid))


def stage(uid):
    with connect() as db:
        r=db.execute('SELECT stage FROM user_workflows WHERE user_id=?',(uid,)).fetchone()
        return r['stage'] if r else ''


def set_stage(uid, stage_value):
    with connect() as db:
        db.execute('''INSERT INTO user_workflows(user_id,stage) VALUES (?,?)
            ON CONFLICT(user_id) DO UPDATE SET stage=excluded.stage''',(uid,stage_value))


def selection(uid, kind):
    p=get(uid)
    return json.loads(p[kind] or '[]') if p else []


def complete(uid):
    p=get(uid)
    return bool(p and p['city'] and p['age'] and selection(uid,'interests') and selection(uid,'skills'))


def ui_data(uid):
    with connect() as db:
        row=db.execute('SELECT data FROM user_workflows WHERE user_id=?',(uid,)).fetchone()
    return json.loads(row['data']) if row else {}


def save_ui_data(uid,data):
    with connect() as db:
        db.execute('INSERT INTO user_workflows(user_id,data) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data',
                   (uid,json.dumps(data,ensure_ascii=False)))


def begin_tags(uid,kind):
    data=ui_data(uid);data[kind]=selection(uid,kind);save_ui_data(uid,data)


def draft_selection(uid,kind):
    return ui_data(uid).get(kind,selection(uid,kind))


def toggle_draft(uid,kind,item):
    picked=draft_selection(uid,kind)
    if item in picked:picked.remove(item)
    elif len(picked)<10:picked.append(item)
    data=ui_data(uid);data[kind]=picked;save_ui_data(uid,data)


def apply_tags(uid,kind):
    picked=draft_selection(uid,kind)
    if not 1<=len(picked)<=10:return False
    set_field(uid,kind,picked)
    return True
