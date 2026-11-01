import os
import sqlite3

PATH = os.getenv('EVENTS_DB', '/data/events_v2.db')
_initialized = False


def connect():
    global _initialized
    os.makedirs(os.path.dirname(PATH) or '.', exist_ok=True)
    db = sqlite3.connect(PATH)
    db.row_factory = sqlite3.Row
    if not _initialized:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT,
                date TEXT,
                place TEXT,
                format TEXT,
                tags TEXT,
                status TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
            CREATE INDEX IF NOT EXISTS idx_events_place ON events(place);
            CREATE INDEX IF NOT EXISTS idx_events_format ON events(format);
        ''')
        _initialized = True
    return db


def count(db):
    return db.execute('SELECT COUNT(*) FROM events').fetchone()[0]


def get(db, index):
    return db.execute('SELECT * FROM events ORDER BY date, id LIMIT 1 OFFSET ?', (index,)).fetchone()
