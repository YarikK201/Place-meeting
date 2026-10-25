import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from catalog import connect, count, one, import_json
from bot import catalog_card, process_update


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.db = connect(self.path / 'test.db')
        self.dataset = [
            {'source_id': 'source-1', 'title': 'Конкурс идей', 'description': 'Подробности', 'event_date': '2026-11-01', 'location': 'Онлайн', 'source_url': 'https://example.org/event/1'},
            {'source_id': 'source-2', 'title': 'Кружок', 'event_date': '2026-11-02', 'source_url': 'https://example.org/event/2'},
        ]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def feed(self, data=None):
        file = self.path / 'events.json'
        file.write_text(json.dumps(self.dataset if data is None else data, ensure_ascii=False), encoding='utf-8')
        return import_json(self.db, file)

    def test_import_twice_no_duplicates(self):
        self.assertEqual(self.feed(), 2)
        self.assertEqual(self.feed(), 2)
        self.assertEqual(count(self.db), 2)

    def test_upsert_updates_record(self):
        self.feed()
        self.dataset[0]['title'] = 'Новое название'
        self.feed()
        self.assertEqual(one(self.db, 0)['title'], 'Новое название')
        self.assertEqual(count(self.db), 2)

    def test_invalid_input_does_not_import_partially(self):
        self.dataset[1]['source_url'] = 'javascript:alert(1)'
        with self.assertRaises(ValueError):
            self.feed()
        self.assertEqual(count(self.db), 0)

    def test_empty_catalog(self):
        text, buttons = catalog_card(self.db, 0, False)
        self.assertIn('нет мероприятий', text)
        self.assertIsNone(buttons)

    def test_navigation_and_link(self):
        self.feed()
        text, buttons = catalog_card(self.db, 0, True)
        self.assertIn('Подробности', text)
        self.assertEqual(buttons[-2][0]['url'], 'https://example.org/event/1')
        self.assertEqual(buttons[0][0]['payload'], 'cat:1:0')

    def test_message_and_button_handlers(self):
        self.feed()
        client = MagicMock()
        update = {'update_type': 'message_created', 'message': {'recipient': {'chat_type': 'dialog'}, 'sender': {'user_id': 123}, 'body': {'text': '/catalog'}}}
        self.assertTrue(process_update(client, self.db, update))
        self.assertEqual(client.send_to_user.call_args.args[0], 123)
        click = {'update_type': 'message_callback', 'callback': {'callback_id': 'test-id', 'payload': 'cat:1:1'}, 'message': {'recipient': {'chat_type': 'dialog'}}}
        self.assertTrue(process_update(client, self.db, click))
        self.assertEqual(client.answer_callback.call_args.args[0], 'test-id')


if __name__ == '__main__':
    unittest.main()
