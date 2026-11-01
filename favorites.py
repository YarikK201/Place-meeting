from catalog import connect


def init():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, event_id)
        )''')


def saved(user_id, event_id):
    with connect() as db:
        return db.execute(
            'SELECT 1 FROM favorites WHERE user_id=? AND event_id=?',
            (user_id, event_id)
        ).fetchone() is not None


def toggle(user_id, event_id):
    with connect() as db:
        exists = db.execute('SELECT 1 FROM favorites WHERE user_id=? AND event_id=?',
                            (user_id, event_id)).fetchone()
        if exists:
            db.execute('DELETE FROM favorites WHERE user_id=? AND event_id=?', (user_id, event_id))
        else:
            db.execute('INSERT OR IGNORE INTO favorites VALUES (?, ?)', (user_id, event_id))


def ids(user_id):
    with connect() as db:
        return [r[0] for r in db.execute('''
            SELECT f.event_id FROM favorites f
            JOIN events e ON e.id=f.event_id
            WHERE f.user_id=? ORDER BY e.date, e.id
        ''', (user_id,))]
