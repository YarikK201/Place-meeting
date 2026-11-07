"""Regression tests for current dev screens (the old tests targeted removed APIs)."""
from test_bot import NavigationCase, app, catalog


class WorkflowTests(NavigationCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        app.profiles.choose(1, 'student')
        with catalog.connect() as db:
            db.execute("INSERT INTO events(id,url,title,description,tags,status) VALUES (5,'https://example.org','Тестовое событие','Описание','[]','published')")

    async def test_catalog_and_favorites(self):
        mid = await self.screen()
        await app.callback(self.click(1, mid, 'card:0'))
        self.assertIn('Тестовое событие', self.transport.edit_message.call_args.kwargs['text'])
        await app.callback(self.click(1, mid, 'favtoggle:5:card:0'))
        await app.callback(self.click(1, mid, 'favorite:0'))
        self.assertIn('Тестовое событие', self.transport.edit_message.call_args.kwargs['text'])
        self.assertTrue(app.fav.saved(1, 5))

    async def test_moderation_and_people_navigation(self):
        with catalog.connect() as db:
            db.execute('INSERT INTO moderator_users(user_id) VALUES (1)')
        mid = await self.screen()
        for action in ['mod:home', 'mod:teachers', 'p:student:0']:
            await app.callback(self.click(1, mid, action))
            text = self.transport.edit_message.call_args.kwargs['text']
            self.assertTrue(text)
        self.assertEqual(app.screen_id(1), mid)

    async def test_message_notification_targets_notification(self):
        app.profiles.choose(2, 'student')
        with catalog.connect() as db:
            db.execute("INSERT INTO friendships(uid1,uid2) VALUES (1,2)")
        await self.screen(2)
        await app.notify(2, 'Тестовое сообщение', app.keyboard([[app.b('Ответить','chat:screen')]]))
        await app.callback(self.click(2, 'm2', 'chat:screen'))
        self.assertEqual(self.transport.edit_message.call_args.kwargs['message_id'], 'm2')

    async def test_direct_message_reply_workflow(self):
        from types import SimpleNamespace as NS
        import asyncio
        app.profiles.choose(2, 'student')
        with catalog.connect() as db:
            db.execute('INSERT INTO friendships(uid1,uid2) VALUES (1,2)')
        mid = await self.screen(1)
        await app.callback(self.click(1, mid, 'dm:write:2'))
        self.assertEqual(app.profiles.stage(1), 'dm_2')
        await app.text_handler(NS(message=NS(sender=NS(user_id=1), body=NS(text='Тест'))))
        await asyncio.gather(*list(app._NOTIFY_TASKS))
        self.assertEqual(app.dm.last(1, 2)['text'], 'Тест')
        with catalog.connect() as db:
            notification = db.execute('SELECT message_id FROM ui_callbacks WHERE user_id=2').fetchone()[0]
        await app.callback(self.click(2, notification, 'dm:reply:1'))
        self.assertEqual(app.profiles.stage(2), 'dm_1')
        self.assertEqual(self.transport.edit_message.call_args.kwargs['message_id'], notification)

    async def test_moderation_notification_approves_teacher(self):
        import asyncio
        app.profiles.choose(2, 'teacher')
        with catalog.connect() as db:
            db.execute('INSERT INTO moderator_users(user_id) VALUES (1)')
            db.execute("INSERT INTO teacher_checks(id,user_id,doc_url,status,created) VALUES (3,2,'https://example.org','pending','2026-11-08')")
        await self.screen(1)
        await app.notify(1, 'Заявка', app.keyboard([[app.b('Проверить', 'mod:teachers')]]))
        await app.callback(self.click(1, 'm2', 'mod:teachers'))
        await app.callback(self.click(1, 'm2', 'mod:teacherok:3'))
        await asyncio.gather(*list(app._NOTIFY_TASKS))
        self.assertEqual(app.profiles.get(2)['teacher_status'], 'approved')
        self.assertEqual(app.screen_id(1), 'm2')
