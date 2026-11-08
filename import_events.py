"""Повторяемый импорт каталога без удаления событий и пользовательских ссылок."""
import json
from pathlib import Path
from urllib.parse import urlparse

from catalog import connect
from event_tags import normalize

ROOT = Path(__file__).resolve().parent


def load_events(path):
    events = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(events, list) or not events:
        raise ValueError('Каталог должен быть непустым списком')
    urls = set()
    for event in events:
        required = {'url', 'title', 'description', 'date', 'place', 'format', 'tags', 'status'}
        if not isinstance(event, dict) or not required <= event.keys():
            raise ValueError('В мероприятии отсутствуют обязательные поля')
        if any(not isinstance(event[k], str) or not event[k].strip()
               for k in ('url', 'title', 'description', 'date', 'place', 'format')):
            raise ValueError('Поля мероприятия должны содержать текст')
        url = urlparse(event['url'])
        if url.scheme not in ('https', 'http') or not url.netloc or event['url'] in urls:
            raise ValueError('Некорректный или повторяющийся URL')
        if event['format'] not in ('онлайн', 'офлайн') or event['status'] not in (None, 'published', 'hidden'):
            raise ValueError('Неизвестный формат или статус')
        tags = event['tags']
        if not isinstance(tags, list) or not tags or any(not normalize([tag]) for tag in tags):
            raise ValueError('Неизвестные или пустые теги')
        urls.add(event['url'])
    return events


def import_catalog(path=ROOT / 'events.json'):
    # Полная проверка до записи: ошибочный файл не должен скрыть весь каталог.
    events = load_events(path)
    urls = {e['url'] for e in events}
    legacy = json.loads((ROOT / 'catalog_legacy_urls.json').read_text(encoding='utf-8'))
    stats = dict(loaded=len(events), imported=0, protected=0, retired=0)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(events)')}
        tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Импорт запускается раньше миграций бота. Защищаем и старую схему,
        # где owner_id ещё нет, по любой истории педагогических заявок.
        protected = set()
        if 'owner_id' in columns:
            protected.update(r[0] for r in db.execute('SELECT id FROM events WHERE owner_id IS NOT NULL'))
        if 'proposed_events' in tables:
            protected.update(r[0] for r in db.execute('SELECT event_id FROM proposed_events WHERE event_id IS NOT NULL'))
        db.execute('CREATE TABLE IF NOT EXISTS imported_catalog (url TEXT PRIMARY KEY)')
        db.executemany('INSERT OR IGNORE INTO imported_catalog VALUES (?)', ((url,) for url in legacy))
        for event in events:
            existing = db.execute('SELECT id FROM events WHERE url=?', (event['url'],)).fetchone()
            if existing and existing['id'] in protected:
                stats['protected'] += 1
                continue
            db.execute('''INSERT INTO events(url,title,description,date,place,format,tags,status)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
                title=excluded.title,description=excluded.description,date=excluded.date,
                place=excluded.place,format=excluded.format,tags=excluded.tags,
                status=COALESCE(events.status,excluded.status)''',
                tuple(event[k] for k in ('url','title','description','date','place','format')) +
                (json.dumps(normalize(event['tags']), ensure_ascii=False), event['status']))
            db.execute('INSERT OR IGNORE INTO imported_catalog VALUES (?)', (event['url'],))
            stats['imported'] += 1
        for row in db.execute('SELECT e.id,e.url,e.status FROM events e JOIN imported_catalog i ON i.url=e.url').fetchall():
            if row['url'] not in urls and row['id'] not in protected and row['status'] != 'hidden':
                db.execute("UPDATE events SET status='hidden' WHERE id=?", (row['id'],))
                stats['retired'] += 1
    return stats


if __name__ == '__main__':
    print('catalog_import', json.dumps(import_catalog(), ensure_ascii=False), flush=True)
