import json
from catalog import connect, count

with open('events.json', encoding='utf-8-sig') as file:
    events = json.load(file)

with connect() as db:
    for e in events:
        db.execute('''
            INSERT INTO events(url, title, description, date, place, format, tags, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              title=excluded.title, description=excluded.description,
              date=excluded.date, place=excluded.place, format=excluded.format,
              tags=excluded.tags, status=excluded.status
        ''', (e['url'], e['title'], e.get('description'), e.get('date'),
              e.get('place'), e.get('format'), json.dumps(e.get('tags', []), ensure_ascii=False),
              e.get('status')))
    print('Мероприятий в базе:', count(db))
