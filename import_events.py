"""Import event JSON into the project's SQLite catalog.

Usage: python import_events.py events.json
Accepts both the new dataset's keys (url/date/place/tags/format/status)
and the original prototype's keys (source_id/source_url/event_date/location).
"""
import json
import sys
from pathlib import Path

from catalog import connect, count, valid_url


def import_events(db, filename):
    data = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("events")
    if not isinstance(data, list):
        raise ValueError("JSON должен содержать массив мероприятий")

    # Extend the existing table without removing any already imported records.
    present = {row[1] for row in db.execute("PRAGMA table_info(events)")}
    for name, sql_type in (("tags", "TEXT"), ("format", "TEXT"), ("status", "TEXT")):
        if name not in present:
            db.execute(f'ALTER TABLE events ADD COLUMN "{name}" {sql_type}')
    db.commit()

    rows = []
    seen = set()
    for i, event in enumerate(data, 1):
        if not isinstance(event, dict):
            raise ValueError(f"Мероприятие №{i}: ожидался объект")

        title = str(event.get("title") or "").strip()
        url = str(event.get("url") or event.get("source_url") or "").strip()
        source_id = str(event.get("source_id") or url).strip()
        if not title or not valid_url(url) or not source_id:
            raise ValueError(f"Мероприятие №{i}: нужны title и корректная ссылка url")
        if source_id in seen:
            raise ValueError(f"Мероприятие №{i}: повторная ссылка/ID: {source_id}")
        seen.add(source_id)

        tags = event.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"Мероприятие №{i}: tags должен быть списком строк")
        tags = list(dict.fromkeys(t.strip() for t in tags if t.strip()))

        # Store the date as text: also supports 'бессрочно', 'с 01.01.2025', etc.
        date = str(event.get("date") or event.get("event_date") or "").strip()
        place = str(event.get("place") or event.get("location") or "").strip()
        description = str(event.get("description") or "").strip()
        fmt = str(event.get("format") or "").strip()
        status = event.get("status")
        status_text = None if status is None else str(status)
        rows.append((source_id, title, description, date, place, url,
                     json.dumps(tags, ensure_ascii=False), fmt, status_text))

    with db:
        db.executemany("""
            INSERT INTO events
              (source_id, title, description, event_date, location, source_url,
               tags, format, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              title=excluded.title,
              description=excluded.description,
              event_date=excluded.event_date,
              location=excluded.location,
              source_url=excluded.source_url,
              tags=excluded.tags,
              format=excluded.format,
              status=excluded.status,
              updated_at=CURRENT_TIMESTAMP
        """, rows)
    return len(rows)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Использование: python import_events.py events.json")
    try:
        with connect() as db:
            processed = import_events(db, sys.argv[1])
            print(f"Обработано: {processed}; всего в базе: {count(db)}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Ошибка импорта: {exc}") from exc


if __name__ == "__main__":
    main()
