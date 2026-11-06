from catalog import connect


def init():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            PRIMARY KEY(user_id,event_id)
        )''')


def saved(uid, eid):
    with connect() as db:
        return db.execute('SELECT 1 FROM favorites WHERE user_id=? AND event_id=?', (uid,eid)).fetchone() is not None


def toggle(uid, eid):
    with connect() as db:
        exists = db.execute('SELECT 1 FROM favorites WHERE user_id=? AND event_id=?', (uid,eid)).fetchone()
        if exists:
            db.execute('DELETE FROM favorites WHERE user_id=? AND event_id=?', (uid,eid))
        else:
            db.execute('INSERT OR IGNORE INTO favorites VALUES (?,?)', (uid,eid))


def ids(uid):
    with connect() as db:
        return [row[0] for row in db.execute('''SELECT f.event_id FROM favorites f
            JOIN events e ON e.id=f.event_id WHERE f.user_id=? ORDER BY e.date,e.id''', (uid,))]
