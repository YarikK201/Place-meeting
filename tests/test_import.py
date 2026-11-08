"""Каталог, миграция импорта и даты: только временная SQLite, без MAX."""
import json
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path
from unittest.mock import patch

import catalog
import filters
import import_events as importer
from presentation import russian_date


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in [('PATH', str(Path(self.temp.name) / 'db.sqlite')), ('_schema_ready', False)]:
            p = patch.object(catalog, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.events = importer.load_events(importer.ROOT / 'events.json')

    def rows(self):
        with catalog.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events ORDER BY id')]

    def test_clean_and_repeated_import_preserves_all_records_and_tags(self):
        self.assertEqual(importer.import_catalog()['imported'], 85)
        before = self.rows()
        self.assertEqual(importer.import_catalog()['retired'], 0)
        self.assertEqual(self.rows(), before)
        self.assertEqual(len(before), 85)
        self.assertEqual(Counter(e['format'] for e in before), {'офлайн': 79, 'онлайн': 6})
        tags = Counter(t for e in before for t in json.loads(e['tags']))
        self.assertEqual(tags['Движение Первых'], 5)
        self.assertEqual(tags['Волонтерство'], 10)
        self.assertEqual(sum(len(json.loads(e['tags'])) == 5 for e in before), 2)
        for source, stored in zip(self.events, before):
            self.assertEqual(json.loads(stored['tags']), source['tags'])

    def test_legacy_retired_favorite_and_unknown_event_preserved(self):
        current = {e['url'] for e in self.events}
        legacy = json.loads((importer.ROOT / 'catalog_legacy_urls.json').read_text())
        old_url = next(url for url in legacy if url not in current)
        with catalog.connect() as db:
            db.execute('INSERT INTO events(id,url,title) VALUES (501,?,?)', (old_url, 'Прежнее событие'))
            db.execute("INSERT INTO events(id,url,title) VALUES (502,'https://example.org/manual','Ручное событие')")
            db.execute('CREATE TABLE favorites (user_id INTEGER,event_id INTEGER)')
            db.execute('INSERT INTO favorites VALUES (7,501)')
        self.assertEqual(importer.import_catalog()['retired'], 1)
        rows = {r['id']: r for r in self.rows()}
        self.assertEqual(rows[501]['status'], 'hidden')
        self.assertIsNone(rows[502]['status'])
        with catalog.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM favorites').fetchone()), (7,501))
        self.assertEqual(importer.import_catalog()['retired'], 0)

    def test_complete_legacy_catalog_transition(self):
        legacy = json.loads((importer.ROOT / 'catalog_legacy_urls.json').read_text())
        with catalog.connect() as db:
            db.executemany('INSERT INTO events(url,title) VALUES (?,?)',
                           ((url,'Старый заголовок') for url in legacy))
        ids = {r['url']:r['id'] for r in self.rows()}
        self.assertEqual(importer.import_catalog()['retired'],54)
        rows = self.rows()
        self.assertEqual(len(rows),139)
        self.assertEqual(sum(r['status'] != 'hidden' for r in rows),85)
        for row in rows:
            if row['url'] in ids:
                self.assertEqual(row['id'],ids[row['url']])
        importer.import_catalog()
        self.assertEqual(self.rows(),rows)

    def test_teacher_owner_and_legacy_history_protected(self):
        current = {e['url'] for e in self.events}
        legacy = json.loads((importer.ROOT / 'catalog_legacy_urls.json').read_text())
        old_url = next(url for url in legacy if url not in current)
        with catalog.connect() as db:
            db.execute('CREATE TABLE proposed_events (event_id INTEGER)')
            for eid, url in [(501, self.events[0]['url']), (502, old_url)]:
                db.execute('INSERT INTO events(id,url,title) VALUES (?,?,?)', (eid,url,'Авторская версия'))
                db.execute('INSERT INTO proposed_events VALUES (?)', (eid,))
        before = self.rows()
        self.assertEqual(importer.import_catalog()['protected'], 1)
        self.assertEqual([r for r in self.rows() if r['id'] in (501,502)], before)
        with catalog.connect() as db:
            db.execute('ALTER TABLE events ADD COLUMN owner_id INTEGER')
            db.execute('DELETE FROM proposed_events')
            db.execute('UPDATE events SET owner_id=9 WHERE id IN (501,502)')
        before = [r for r in self.rows() if r['id'] in (501,502)]
        self.assertEqual(importer.import_catalog()['protected'], 1)
        self.assertEqual([r for r in self.rows() if r['id'] in (501,502)], before)

    def test_future_catalog_removal_and_moderator_hidden_status(self):
        importer.import_catalog()
        with catalog.connect() as db:
            db.execute("UPDATE events SET status='hidden' WHERE url=?", (self.events[1]['url'],))
        source = Path(self.temp.name) / 'next.json'
        source.write_text(json.dumps(self.events[1:]), encoding='utf-8')
        self.assertEqual(importer.import_catalog(source)['retired'], 1)
        importer.import_catalog()
        # Возвращение в JSON не отменяет скрытие; восстановление — у модератора.
        self.assertEqual([r['status'] for r in self.rows()[:2]], ['hidden','hidden'])

    def test_invalid_input_does_not_change_database(self):
        importer.import_catalog()
        before = self.rows()
        path = Path(self.temp.name) / 'bad.json'
        for data in [[], [self.events[0], self.events[0]], [dict(self.events[0], format='unknown')],
                     [dict(self.events[0], tags=['неизвестно'])]]:
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                importer.import_catalog(path)
            self.assertEqual(self.rows(), before)

    def test_date_ranges_indefinite_projects_and_formats(self):
        importer.import_catalog()
        state = dict(topics=[], ages=[], city='', period='', format='')
        with patch.object(filters, 'today', return_value=date(2026,11,8)):
            rows = self.rows()
            self.assertEqual(sum(filters.matches(r,state) for r in rows), 85)
            for fmt, count in [('онлайн',6), ('офлайн',79)]:
                self.assertEqual(sum(filters.matches(r,dict(state,format=fmt)) for r in rows),count)
            self.assertEqual(sum(filters.matches(r,dict(state,period='anytime')) for r in rows),10)
            for topic, count in [('Движение Первых',5), ('Волонтерство',10)]:
                self.assertEqual(sum(filters.matches(r,dict(state,topics=[topic])) for r in rows),count)
            event = dict(rows[0], date='с 01.01.2025')
            self.assertTrue(filters.matches(event,dict(state,period='today')))
            self.assertFalse(filters.matches(dict(event,date='с 01.01.2027'),dict(state,period='today')))
            for separator in ['-', '—']:
                value = f'2026-09-30 {separator} 2026-12-20'
                self.assertEqual(filters.dates(value),(date(2026,9,30),date(2026,12,20)))
                self.assertTrue(filters.matches(dict(event,date=value),dict(state,period='today')))
                self.assertEqual(russian_date(value),'С 30 сентября по 20 декабря 2026')
            self.assertFalse(filters.matches(dict(event,date='2026-11-07'),state))


if __name__ == '__main__':
    unittest.main()
