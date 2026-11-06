import json
from catalog import connect

with open('events.json', encoding='utf-8-sig') as file:
    events = json.load(file)

with connect() as db:
    for event in events:
        db.execute('''INSERT INTO events(url,title,description,date,place,format,tags,status)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
            title=excluded.title,description=excluded.description,
            date=excluded.date,place=excluded.place,format=excluded.format,
            tags=excluded.tags,status=COALESCE(events.status,excluded.status)''',
            (event['url'], event['title'], event.get('description'),
             event.get('date'), event.get('place'), event.get('format'),
             json.dumps(event.get('tags', []), ensure_ascii=False), event.get('status')))
