"""SQLite: схема создаётся один раз на процесс, соединения всегда закрываются."""
import os
import sqlite3
import logging
from time import perf_counter
from threading import Lock

PATH = os.getenv('EVENTS_DB', '/data/events_v2.db')
_schema_ready = False
_schema_lock = Lock()


class ClosingConnection(sqlite3.Connection):
    """Контекстный менеджер закрывает sqlite-соединение даже при ошибке."""
    def __init__(self, *args, **kwargs):
        self.started = perf_counter()
        super().__init__(*args, **kwargs)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
            logging.getLogger("place_meeting.db").info(
                "db elapsed_ms=%.1f", (perf_counter() - self.started) * 1000)


def connect():
    global _schema_ready
    os.makedirs(os.path.dirname(PATH) or '.', exist_ok=True)
    db = sqlite3.connect(PATH, factory=ClosingConnection, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        if not _schema_ready:
            with _schema_lock:
                if not _schema_ready:
                    db.execute('''CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY,
                        url TEXT NOT NULL UNIQUE,
                        title TEXT NOT NULL,
                        description TEXT,
                        date TEXT,
                        place TEXT,
                        format TEXT,
                        tags TEXT,
                        status TEXT
                    )''')
                    db.commit()
                    _schema_ready = True
        return db
    except BaseException:
        db.close()
        raise
