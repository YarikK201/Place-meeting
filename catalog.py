"""SQLite event catalog and idempotent JSON import."""
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = os.environ.get('EVENTS_DB', '/data/events.db')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    event_date TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date, id);
'''


def connect(path=None):
    path = str(path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def valid_url(value):
    parsed = urlparse(value)
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc) and not parsed.username and not parsed.password


def import_json(db, file_path):
    with open(file_path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get('events')
    if not isinstance(data, list):
        raise ValueError('JSON must be a list or an object with events list')
    rows = []
    seen = set()
    for number, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'Event {number}: expected an object')
        source_id = str(item.get('source_id') or '').strip()
        title = str(item.get('title') or '').strip()
        url = str(item.get('source_url') or '').strip()
        if not source_id or not title or not valid_url(url):
            raise ValueError(f'Event {number}: source_id, title and valid HTTPS/HTTP source_url required')
        if source_id in seen:
            raise ValueError(f'Event {number}: duplicate source_id in input: {source_id}')
        seen.add(source_id)
        rows.append((source_id, title, str(item.get('description') or ''), str(item.get('event_date') or ''), str(item.get('location') or ''), url))
    with db:
        db.executemany('''
            INSERT INTO events(source_id,title,description,event_date,location,source_url)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(source_id) DO UPDATE SET
              title=excluded.title, description=excluded.description,
              event_date=excluded.event_date, location=excluded.location,
              source_url=excluded.source_url, updated_at=CURRENT_TIMESTAMP
        ''', rows)
    return len(rows)


def count(db):
    return db.execute('SELECT COUNT(*) FROM events').fetchone()[0]


def one(db, index):
    return db.execute('''SELECT * FROM events ORDER BY event_date, id LIMIT 1 OFFSET ?''', (index,)).fetchone()
