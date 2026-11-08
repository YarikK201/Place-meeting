import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch
from test_bot import NavigationCase, app, catalog


class BusinessTests(NavigationCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        app.profiles.choose(1,'teacher','Педагог')
        app.profiles.set_field(1,'teacher_status','approved')
        app.profiles.choose(2,'student','Анна')
        with catalog.connect() as db:
            db.execute('INSERT INTO moderator_users VALUES (9)')
            db.execute('INSERT INTO moderator_users VALUES (10)')
            db.execute('INSERT INTO friendships VALUES (1,2)')
        self.data=dict(title='Старая версия',description='Описание',date='2030-12-01',
                       place='Город',format='онлайн',tags=[next(iter(app.te.ALL_TAGS))],url='https://example.org/one')

    def publish(self):
        rid=app.te.start_draft(1)
        for k,v in self.data.items():app.te.set_draft(1,k,v)
        self.assertTrue(app.te.send_review(1))
        self.assertEqual(app.te.decide_event(9,rid,True),1)
        with catalog.connect() as db:
            return db.execute('SELECT event_id FROM proposed_events WHERE id=?',(rid,)).fetchone()[0]

    def event(self,eid):
        with catalog.connect() as db:return dict(db.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone())

    def changes(self,eid):
        rid=app.te.start_draft(1,eid)
        self.assertIsNotNone(rid)
        app.te.set_draft(1,'title','Новая версия')
        self.assertTrue(app.te.send_review(1))
        return rid

    async def test_approve_preserves_id_and_favorites(self):
        eid=self.publish();app.fav.toggle(2,eid)
        rid=self.changes(eid)
        self.assertEqual(self.event(eid)['title'],'Старая версия')
        self.assertEqual(app.te.status_label(app.te.my_events(1)[0]),'На рассмотрении')
        self.assertEqual(len(app.te.my_events(1)),1)
        self.assertEqual(app.te.decide_event(9,rid,True),1)
        self.assertEqual(self.event(eid)['title'],'Новая версия')
        self.assertTrue(app.fav.saved(2,eid))
        self.assertIsNone(app.te.decide_event(9,rid,True))
        with catalog.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)

    async def test_reject_preserves_entire_published_record(self):
        eid=self.publish();before=self.event(eid);rid=self.changes(eid)
        self.assertEqual(app.te.decide_event(9,rid,False,'Уточните'),1)
        self.assertEqual(self.event(eid),before)
        self.assertEqual(app.te.status_label(app.te.my_events(1)[0]),'Опубликовано')
        self.assertEqual(app.te.my_events(1)[0]['comment'],'Уточните')
        self.assertIsNone(app.te.decide_event(9,rid,True))

    async def test_hide_restore_with_pending_edit_and_favorite(self):
        eid=self.publish();app.fav.toggle(2,eid);rid=self.changes(eid)
        await app.dispatch(9,['mod','hide',str(eid)])
        self.assertEqual(self.event(eid)['status'],'hidden')
        self.assertNotIn(eid,[r['id'] for r in app.flt.results_unfiltered()])
        self.assertNotIn('Старая версия',app.event_card(0,2,'favorite')[0])
        self.assertTrue(app.fav.saved(2,eid))
        self.assertIn('Восстановить',str(app.mod_screen(9,'published')[1]))
        app.te.decide_event(9,rid,True)
        self.assertEqual(app.te.status_label(app.te.my_events(1)[0]),'Скрыт')
        await app.dispatch(9,['mod','restore',str(eid)])
        self.assertEqual(app.te.status_label(app.te.my_events(1)[0]),'Опубликовано')
        self.assertIn('Новая версия',app.event_card(0,2,'favorite')[0])
        self.assertEqual(app.fav.ids(2),[eid])

    async def test_edit_hidden_and_reject_keeps_hidden(self):
        eid=self.publish();app.te.set_visibility(9,eid,True)
        rid=self.changes(eid);app.te.decide_event(9,rid,False)
        self.assertEqual(self.event(eid)['status'],'hidden')
        self.assertEqual(self.event(eid)['title'],'Старая версия')

    async def test_foreign_ownership_and_url_collision(self):
        eid=self.publish()
        app.profiles.choose(3,'teacher');app.profiles.set_field(3,'teacher_status','approved')
        self.assertIsNone(app.te.start_draft(3,eid))
        rid=app.te.start_draft(3)
        for k,v in self.data.items():app.te.set_draft(3,k,v)
        self.assertTrue(app.te.send_review(3))
        self.assertIsNone(app.te.decide_event(9,rid,True))
        self.assertEqual(self.event(eid)['owner_id'],1)
        self.assertFalse(app.te.set_visibility(3,eid,True))

    async def test_concurrent_moderators_apply_once(self):
        eid=self.publish();rid=self.changes(eid)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda mod:app.te.decide_event(mod,rid,True),[9,10]))
        self.assertCountEqual(results,[1,None])
        self.assertEqual(self.event(eid)['title'],'Новая версия')

    async def test_separate_drafts_not_confused(self):
        eid=self.publish();rid=app.te.start_draft(1,eid)
        self.assertIsNone(app.te.start_draft(1))
        self.assertEqual(app.te.draft(1)['id'],rid)
        app.te.set_draft(1,'title','Изменения');app.te.send_review(1)
        self.assertIsNone(app.te.start_draft(1,eid))
        self.assertIsNotNone(app.te.start_draft(1))

    async def test_report_expires_and_preserves_messages(self):
        app.social.start(1,2)
        self.assertTrue(app.dm.send(2,1,'Сообщение')[0])
        now=datetime(2026,11,8,tzinfo=timezone.utc)
        with patch.object(app.social,'UTC',return_value=now):
            self.assertTrue(app.dm.report(1,2,'Спам'))
            self.assertFalse(app.dm.report(1,2,'Повтор'))
            self.assertFalse(app.social.friends(1,2))
            self.assertIsNone(app.social.partner(1))
            self.assertFalse(app.dm.send(2,1,'Обход')[0])
            self.assertFalse(app.dm.send(1,2,'Обход')[0])
            self.assertIsNotNone(app.dm.last(2,1))
        with patch.object(app.social,'UTC',return_value=now+timedelta(days=7)-timedelta(seconds=1)):
            self.assertTrue(app.social.blocked(1,2))
        with patch.object(app.social,'UTC',return_value=now+timedelta(days=7)):
            self.assertFalse(app.social.blocked(1,2))
            self.assertTrue(app.dm.allowed(2,1))
            self.assertFalse(app.social.friends(1,2))

    async def test_report_notifications_and_clicked_message(self):
        app.dm.send(2,1,'Спам')
        old=await self.screen(2)
        mid=await self.screen(1)
        await app.callback(self.click(1,mid,'dm:reason:2:2'))
        await asyncio.gather(*list(app._NOTIFY_TASKS))
        sent=[c.kwargs for c in self.transport.send_message.call_args_list if 'подал жалобу' in c.kwargs['text']]
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0]['user_id'],2)
        self.assertIn('Педагог подал жалобу',sent[0]['text'])
        self.assertIn('7 дней',sent[0]['text'])
        self.assertIn('Жалоба отправлена',self.transport.edit_message.call_args.kwargs['text'])
        with catalog.connect() as db:
            notification=db.execute('SELECT message_id FROM ui_callbacks WHERE user_id=2 AND message_id!=?',(old,)).fetchone()[0]
        await app.callback(self.click(2,notification))
        self.assertEqual(self.transport.edit_message.call_args.kwargs['message_id'],notification)

    async def test_repeat_initialization_preserves_data(self):
        eid=self.publish();app.fav.toggle(2,eid);app.dm.send(2,1,'Сохранить')
        with catalog.connect() as db:db.execute("UPDATE direct_messages SET sent_at='2020-01-01T00:00:00+00:00'")
        app.social.report(1,2,'Причина');rid=self.changes(eid)
        with catalog.connect() as db:
            before={t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in ['events','proposed_events','profiles','reports','interactions_blocked','favorites','direct_messages']}
        for _ in range(2):app.social.init();app.te.init();app.dm.init()
        with catalog.connect() as db:
            for t,rows in before.items():self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM '+t)],rows,t)
        self.assertEqual(app.te.my_events(1)[0]['id'],rid)

    async def test_legacy_migration(self):
        eid=self.publish()
        with catalog.connect() as db:
            db.execute('ALTER TABLE events DROP COLUMN owner_id')
            db.execute('ALTER TABLE interactions_blocked DROP COLUMN expires_at')
            db.execute('INSERT INTO interactions_blocked VALUES (1,2)')
            db.execute("INSERT INTO reports(reporter,reported,reason,created) VALUES (1,2,'legacy','2020-01-01T00:00:00+00:00')")
        app.social.init();app.te.init();app.social.init();app.te.init()
        self.assertEqual(self.event(eid)['owner_id'],1)
        self.assertFalse(app.social.blocked(1,2))

    async def test_imported_event_visibility(self):
        with catalog.connect() as db:
            eid=db.execute("INSERT INTO events(url,title) VALUES ('https://example.org/import','Импорт')").lastrowid
        self.assertTrue(app.te.set_visibility(9,eid,True))
        self.assertTrue(app.te.set_visibility(9,eid,False))
        self.assertEqual(self.event(eid)['status'],'published')

    async def test_approval_notification_edits_itself(self):
        eid=self.publish();rid=self.changes(eid)
        old=await self.screen(1)
        mid=await self.screen(9)
        await app.callback(self.click(9,mid,f'mod:eventok:{rid}'))
        await asyncio.gather(*list(app._NOTIFY_TASKS))
        with catalog.connect() as db:
            notification=db.execute('SELECT message_id FROM ui_callbacks WHERE user_id=1 AND message_id!=?',(old,)).fetchone()[0]
        await app.callback(self.click(1,notification,'te:mine'))
        self.assertEqual(self.transport.edit_message.call_args.kwargs['message_id'],notification)

    async def test_forged_edit_cannot_change_other_owner(self):
        eid=self.publish()
        with catalog.connect() as db:
            rid=db.execute("INSERT INTO proposed_events(teacher_id,event_id,draft,status,created) VALUES (3,?,?,'pending','2026')",(eid,json.dumps(self.data))).lastrowid
        self.assertIsNone(app.te.decide_event(9,rid,True))
        self.assertEqual(self.event(eid)['title'],'Старая версия')

    async def test_free_text_report_confirms_and_notifies(self):
        app.dm.send(2,1,'Текст')
        app.profiles.set_stage(1,'dmreport_2')
        await app.text_handler(NS(message=NS(sender=NS(user_id=1),body=NS(text='Причина'))))
        await asyncio.gather(*list(app._NOTIFY_TASKS))
        messages=[c.kwargs for c in self.transport.send_message.call_args_list]
        self.assertTrue(any(m['user_id']==1 and 'Жалоба отправлена' in m['text'] for m in messages))
        self.assertTrue(any(m['user_id']==2 and 'Причина' in m['text'] for m in messages))

    async def test_conversation_report_and_reentry_blocked(self):
        app.social.start(1,2)
        mid=await self.screen(2)
        click=self.click(2,mid,'chat:reason:2')
        click.callback.user.first_name='Анна'
        await app.callback(click)
        await asyncio.gather(*list(app._NOTIFY_TASKS))
        self.assertIsNone(app.social.partner(1))
        self.assertFalse(app.social.friends(1,2))
        self.assertEqual(app.social.start(1,2),'Общение недоступно.')
        sent=[c.kwargs for c in self.transport.send_message.call_args_list]
        self.assertTrue(any(m['user_id']==1 and 'Анна подал жалобу' in m['text'] for m in sent))
        self.assertIn('Жалоба отправлена',self.transport.edit_message.call_args.kwargs['text'])
