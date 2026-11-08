import asyncio
import json
from datetime import datetime, date
from types import SimpleNamespace as NS
from unittest.mock import patch
from test_bot import NavigationCase, app, catalog
from presentation import event_text, russian_date, parse_teacher_date, Markdown


def rows(screen):
    return [[(b.text,b.payload) for b in r if getattr(b,'type',None)=='callback'] for r in screen[1][0].payload.buttons]


def payloads(screen):return [p for r in rows(screen) for _,p in r]


class InterfaceTests(NavigationCase):
    async def student(self):
        app.profiles.choose(1,'student')
        return await self.screen(1)

    def teacher(self):
        app.profiles.choose(1,'teacher','Иванов Иван Иванович')
        app.profiles.set_field(1,'teacher_status','approved')
        with catalog.connect() as db:
            db.execute("UPDATE profiles SET teacher_workplace='Школа',teacher_position='Учитель' WHERE user_id=1")

    async def test_01_first_profile_new_messages(self):
        mid=await self.student()
        await app.callback(self.click(1,mid,'profile:edit'))
        self.assertEqual(app.profiles.stage(1),'wizard_city')
        self.assertNotEqual(app.screen_id(1),mid)
        self.assertEqual(self.transport.send_message.call_args.kwargs['text'],'📍В каком городе ты живешь?')
        for text,expected in [('Город','wizard_age'),('15','wizard_interests')]:
            old=app.screen_id(1)
            await app.text_handler(NS(message=NS(sender=NS(user_id=1),body=NS(text=text))))
            self.assertEqual(app.profiles.stage(1),expected)
            self.assertNotEqual(app.screen_id(1),old)
        for kind,expected in [('interests','wizard_skills'),('skills','')]:
            await app.callback(self.click(1,app.screen_id(1),f'tags:toggle:{kind}:0:0'))
            self.assertEqual(app.profiles.selection(1,kind),[])
            await app.callback(self.click(1,app.screen_id(1),f'tags:{kind}'))
            old=app.screen_id(1)
            await app.callback(self.click(1,old,f'tags:done:{kind}'))
            self.assertEqual(app.profiles.stage(1),expected)
            self.assertNotEqual(app.screen_id(1),old)
        self.assertTrue(app.profiles.complete(1))

    async def test_01_02_limits_and_categories(self):
        await self.student()
        for kind,source,warning in [('interests',app.profiles.INTERESTS,'Тебя интересует очень многое!'),('skills',app.profiles.SKILLS,'Ты столько всего знаешь!')]:
            app.profiles.begin_tags(1,kind)
            self.assertNotIn(f'tags:done:{kind}',payloads(app.tags_screen(1,kind)))
            choices=[x for group in source.values() for x in group]
            for item in choices[:11]:app.profiles.toggle_draft(1,kind,item)
            self.assertEqual(len(app.profiles.draft_selection(1,kind)),10)
            self.assertIn(warning,app.tags_screen(1,kind)[0])
            self.assertIn(f'tags:{kind}',payloads(app.tags_screen(1,kind,0)))
            self.assertNotIn(f'tags:done:{kind}',payloads(app.tags_screen(1,kind,0)))
            self.assertEqual(app.profiles.selection(1,kind),[])
            app.profiles.apply_tags(1,kind)
            self.assertEqual(len(app.profiles.selection(1,kind)),10)
            app.profiles.toggle_draft(1,kind,choices[0])
            self.assertEqual(len(app.profiles.draft_selection(1,kind)),9)

    async def test_03_edit_complete_profile(self):
        await self.student()
        app.profiles.set_field(1,'city','Город');app.profiles.set_field(1,'age',15)
        for kind,source in [('interests',app.profiles.INTERESTS),('skills',app.profiles.SKILLS)]:
            app.profiles.set_field(1,kind,[next(iter(source.values()))[0]])
        screen=await app.dispatch(1,['profile','edit'])
        self.assertEqual(screen[0],'✏️ Что хочешь изменить?')
        self.assertIn('profile:field:age',payloads(screen))

    async def test_05_max_name_on_role_creation(self):
        mid=await self.screen()
        event=self.click(1,mid,'role:student');event.callback.user.first_name='Анна';event.callback.user.last_name='Иванова'
        await app.callback(event)
        self.assertEqual(app.profiles.get(1)['name'],'Анна Иванова')
        app.remember_sender(NS(user_id=1))
        self.assertEqual(app.name(1),'Анна Иванова')
        self.assertEqual(app.name(123),'Участник')

    async def test_06_only_students_and_08_filter_text(self):
        self.teacher();app.profiles.choose(2,'teacher');app.profiles.choose(3,'student')
        with catalog.connect() as db:
            db.execute("UPDATE profiles SET waiting=0,teacher_status='approved'")
            db.executemany('INSERT INTO people_settings(user_id,visible) VALUES (?,1)',[(2,),(3,)])
        self.assertEqual([r['user_id'] for r in app.people.list_people(1,'teacher')],[3])
        self.assertNotIn('p:teacher:0',payloads(app.people_screen(1)))
        self.assertNotIn('Город на этапе MVP',app.people_filters(1)[0])

    async def test_02_people_filter_done_returns_to_categories(self):
        await self.student()
        app.people.begin_filter(1,'interests')
        option=next(iter(app.profiles.INTERESTS.values()))[0]
        app.people.toggle_filter(1,'interests',option)
        screen=app.person_filter_screen(1,'interests',0)
        self.assertIn('pf:group:interests:-1',payloads(screen))
        self.assertNotIn('pf:save:interests',payloads(screen))
        self.assertIn(option,screen[0])
        self.assertEqual(app.people.get_values(1,'interests'),[])
        await app.dispatch(1,['pf','save','interests'])
        self.assertEqual(app.people.get_values(1,'interests'),[option])

    async def test_07_teacher_identity_and_15_spacing(self):
        self.teacher();app.profiles.choose(2,'student')
        app.dm.send(1,2,'Ранее')  # publish student so teacher can initiate
        with catalog.connect() as db:db.execute('INSERT INTO people_settings(user_id,visible) VALUES (2,1)')
        app.dm.send(1,2,'Ранее')
        screen=await app.dispatch(2,['dm','reply','1'])
        for text in ['Педагог','Иванов Иван Иванович','Школа','Учитель','\n\nРанее']:self.assertIn(text,screen[0])

    async def test_09_role_rows(self):
        await self.student()
        self.assertEqual([p for r in rows(app.role_screen(1)) for _,p in r],['role:select','home'])
        self.assertEqual(len(rows(app.role_screen(1))),2)

    async def test_12_verification_next_field_and_13_pending(self):
        app.profiles.choose(1,'teacher')
        text,buttons=await app.dispatch(1,['te','verify','full_name'])
        self.assertIn('Укажите',text)
        await app.text_handler(NS(message=NS(sender=NS(user_id=1),body=NS(text='Иванов Иван'))))
        self.assertEqual(app.profiles.stage(1),'teacher_workplace')
        app.profiles.set_field(1,'teacher_status','pending')
        screen=app.teacher_screen(1)
        self.assertIn('Ваша заявка на подтверждение статуса педагога получена.',screen[0])
        self.assertEqual(payloads(screen),['card:0','home'])

    async def test_14_rejected_friend_notification(self):
        await self.student();app.profiles.set_field(1,'name','Анна');app.profiles.choose(2,'student')
        with catalog.connect() as db:db.execute("INSERT INTO friendship_requests VALUES (2,1,'pending','2026')")
        await app.dispatch(1,['req','reject','2']);await asyncio.gather(*list(app._NOTIFY_TASKS))
        args=self.transport.send_message.call_args.kwargs
        self.assertIn('Анна не принял твою заявку в друзья',args['text'])
        self.assertEqual(len(args['attachments'][0].payload.buttons),2)

    async def test_16_teacher_approval_home_button(self):
        self.teacher()
        with catalog.connect() as db:
            db.execute('INSERT INTO moderator_users VALUES (9)')
            db.execute("INSERT INTO teacher_checks(id,user_id,doc_url,created) VALUES (1,1,'https://example.org','2026')")
        await app.dispatch(9,['mod','teacherok','1']);await asyncio.gather(*list(app._NOTIFY_TASKS))
        args=self.transport.send_message.call_args.kwargs
        self.assertTrue(args['attachments'][0].payload.buttons[0][0].payload.endswith(':home'))

    async def test_17_new_event_and_21_teacher_menu(self):
        self.teacher()
        self.assertEqual([r[0][1] for r in rows(app.menu(1))],['card:0','p:student:0','te:new','te:mine','dm:sent:0'])
        screen=await app.dispatch(1,['te','new'])
        self.assertEqual(app.profiles.stage(1),'event_title')
        self.assertNotIn('Продолжить',str(screen))

    async def test_19_moderator_rows(self):
        with catalog.connect() as db:db.execute('INSERT INTO moderator_users VALUES (9)')
        screen=app.mod_screen(9)
        self.assertEqual(rows(screen)[0][0][1],'mod:teachers')
        self.assertEqual(rows(screen)[1][0][1],'mod:events')
        self.assertNotIn('home',payloads(screen))

    async def test_22_event_topic_categories(self):
        self.teacher();app.te.start_draft(1)
        group=next(iter(app.et.GROUPS))
        await app.dispatch(1,['te','topictoggle','new',group,'0'])
        screen=app.event_topic_screen(1,group=group)
        self.assertIn('te:topicgroups:new',payloads(screen))
        self.assertNotIn('te:topicdone:new',payloads(screen))
        self.assertIn(app.et.GROUPS[group][1][0],screen[0])
        self.assertIn('te:topicdone:new',payloads(app.event_topic_screen(1)))

    async def test_04_18_shared_card_and_transport_mode(self):
        self.teacher()
        data=dict(title='A * [link](url)',description='**unsafe**',date='2030-12-01',place='Город',format='онлайн',tags=['Тест'])
        rendered=event_text(data)
        self.assertIsInstance(rendered,Markdown)
        self.assertIn(r'\*',rendered)
        self.assertIn('### 📅 Когда: 1 декабря 2030',rendered)
        with catalog.connect() as db:
            eid=db.execute("INSERT INTO events(url,title,date,tags,status) VALUES ('https://example.org','Карточка','2030-12-01','[]','published')").lastrowid
            db.execute("INSERT INTO proposed_events(teacher_id,event_id,draft,status,created) VALUES (1,?,?,'pending','2030')",(eid,json.dumps(data)))
            db.execute('INSERT INTO moderator_users VALUES (9)')
        for screen in [app.event_card(0,1),app.mod_screen(9,'events'),await app.dispatch(1,['te','view','1'])]:
            self.assertIsInstance(screen[0],Markdown)
            self.assertIn('# 🎯',screen[0])
        await app.show_screen(1,rendered)
        self.assertEqual(self.transport.send_message.call_args.kwargs['format'],'markdown')
        await app.show_screen(1,rendered)
        self.assertEqual(self.transport.edit_message.call_args.kwargs['format'],'markdown')

    async def test_04_dates(self):
        for raw,expected in [('2026-12-01','1 декабря 2026'),('2026-12-01 - 2026-12-13','С 1 по 13 декабря 2026'),('2026-11-01 - 2026-12-13','С 1 ноября по 13 декабря 2026'),('2026-01-01 - 2027-01-01','С 1 января 2026 по 1 января 2027')]:
            with self.subTest(raw=raw):self.assertEqual(russian_date(raw),expected)
        self.assertEqual(russian_date('2026-99-99'),'2026-99-99')

    async def test_26_date_validation(self):
        now=datetime(2026,1,1,10,0)
        for raw in ['31.02.26','29.02.27','01.01.26 09.59','01.01.25','01.01.26 24.00','02.01.26 - 01.01.26','01.01.26 - 01.01.26','2026-01-01']:
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_teacher_date(raw,now)
        for raw,expected in [('29.02.28','2028-02-29'),('01.01.26 10.30','2026-01-01 10:30'),('01.01.26 - 03.01.26','2026-01-01 - 2026-01-03')]:
            self.assertEqual(parse_teacher_date(raw,now),expected)
        self.teacher();app.te.start_draft(1);app.profiles.set_stage(1,'event_date')
        await app.text_handler(NS(message=NS(sender=NS(user_id=1),body=NS(text='31.02.30'))))
        self.assertEqual(app.profiles.stage(1),'event_date')
        self.assertNotIn('date',app.event_data(1))

    async def test_24_date_sort(self):
        with catalog.connect() as db:
            for i,d in enumerate(['2030-01-01 - 2030-12-31','2030-02-02','2030-02-01']):
                db.execute("INSERT INTO events(url,title,date,tags,status) VALUES (?,?,?,'[]','published')",(f'https://example.org/{i}',str(i),d))
        app.flt.update(1,'period','2030-02-02')
        with patch.object(app.flt,'today',return_value=date(2030,1,1)):
            self.assertEqual([r['title'] for r in app.flt.results(1)],['1','0'])

    async def test_20_23_event_notifications(self):
        self.teacher()
        with catalog.connect() as db:db.execute('INSERT INTO moderator_users VALUES (9)')
        data=dict(title='Название',description='Текст',date='2030-12-01',place='Город',format='онлайн',tags=[next(iter(app.et.ALL_TAGS))],url='https://example.org')
        for approve in [False,True]:
            rid=app.te.start_draft(1)
            for k,v in data.items():app.te.set_draft(1,k,v)
            app.te.send_review(1)
            if approve:
                await app.dispatch(9,['mod','eventok',str(rid)])
                await asyncio.gather(*list(app._NOTIFY_TASKS))
            else:
                app.profiles.set_stage(9,'modevent_'+str(rid))
                await app.text_handler(NS(message=NS(sender=NS(user_id=9),body=NS(text='Комментарий'))))
            sent=[c.kwargs for c in self.transport.send_message.call_args_list if c.kwargs['user_id']==1][-1]
            self.assertIn('Название',sent['text'])
            expected=['card:0','te:mine'] if approve else ['te:new','home']
            actual=[r[0].payload.split(':',2)[2] for r in sent['attachments'][0].payload.buttons]
            self.assertEqual(actual,expected)
            self.assertIn('опубликовано в каталоге' if approve else 'Комментарий',sent['text'])

    async def test_24_unknown_and_invalid_dates_are_safe(self):
        with catalog.connect() as db:
            for i,d in enumerate(['Не определено','2030-99-99','2030-02-02']):
                db.execute("INSERT INTO events(url,title,date,tags) VALUES (?,?,?,'[]')",(f'https://example.org/{i}',str(i),d))
        app.flt.update(1,'period','2030-02-02')
        self.assertEqual([r['title'] for r in app.flt.results(1)],['2','0','1'])
