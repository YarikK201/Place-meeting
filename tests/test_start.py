import asyncio
from types import SimpleNamespace as NS
from unittest.mock import patch
from maxapi.types import BotStarted
from test_bot import NavigationCase, app


class StartTests(NavigationCase):
    def event(self,uid=1,timestamp=100):
        return BotStarted(update_type='bot_started',timestamp=timestamp,chat_id=900,
                          user={'user_id':uid,'first_name':'Тест','is_bot':False})

    def command(self,uid=1):
        return NS(message=NS(sender=NS(user_id=uid,first_name='Тест')))

    async def test_first_system_start_has_role_buttons(self):
        await app.bot_started(self.event())
        args=self.transport.send_message.call_args.kwargs
        self.assertEqual(args['user_id'],1)
        self.assertIn('🎯 Место встречи изменить нельзя.',args['text'])
        buttons=args['attachments'][0].payload.buttons[0]
        self.assertTrue(buttons[0].payload.endswith(':role:student'))
        self.assertTrue(buttons[1].payload.endswith(':role:teacher'))
        self.assertIsNotNone(app.screen_id(1))

    async def test_concurrent_duplicate_and_stale_system_events(self):
        await asyncio.gather(app.bot_started(self.event()),app.bot_started(self.event()))
        await app.bot_started(self.event(timestamp=99))
        self.transport.send_message.assert_awaited_once()
        self.transport.edit_message.assert_not_awaited()
        app._ACTION_LOCKS.clear()
        await app.bot_started(self.event())
        self.transport.send_message.assert_awaited_once()

    async def test_new_system_event_reuses_screen(self):
        await app.bot_started(self.event())
        mid=app.screen_id(1)
        await app.bot_started(self.event(timestamp=101))
        self.transport.send_message.assert_awaited_once()
        self.assertEqual(self.transport.edit_message.call_args.kwargs['message_id'],mid)

    async def test_registered_user_gets_menu(self):
        app.profiles.choose(1,'student')
        await app.bot_started(self.event())
        self.assertEqual(self.transport.send_message.call_args.kwargs['text'],'🏠 Приветствуем тебя на Месте встречи! Ну что, какие планы?')
        await app.start(self.command())
        self.assertEqual(self.transport.edit_message.call_args.kwargs['text'],'🏠 Приветствуем тебя на Месте встречи! Ну что, какие планы?')

    async def test_start_command_new_user_and_system_event(self):
        await app.start(self.command())
        await app.bot_started(self.event())
        self.transport.send_message.assert_awaited_once()
        self.assertIn('🎯 Место встречи изменить нельзя.',self.transport.edit_message.call_args.kwargs['text'])

    async def test_rapid_start_commands_do_not_duplicate(self):
        await asyncio.gather(app.start(self.command()),app.start(self.command()))
        self.transport.send_message.assert_awaited_once()
        with patch.object(app,'time',return_value=app.profiles.ui_data(1)['last_open_at']+4):
            await app.start(self.command())
        self.assertEqual(self.transport.send_message.await_count,2)

    async def test_failure_not_replayed_command_recovers(self):
        self.transport.send_message.side_effect=TimeoutError()
        await app.bot_started(self.event())
        await app.bot_started(self.event())
        self.transport.send_message.assert_awaited_once()
        self.transport.send_message.side_effect=self.send
        await app.start(self.command())
        self.assertIsNotNone(app.screen_id(1))

    async def test_different_users_independent(self):
        await asyncio.gather(app.bot_started(self.event(1)),app.bot_started(self.event(2)))
        self.assertNotEqual(app.screen_id(1),app.screen_id(2))

    async def test_role_command_for_student_and_teacher(self):
        for uid, role in [(1, 'student'), (2, 'teacher')]:
            with self.subTest(role=role):
                app.profiles.choose(uid, role)
                old_mid = await self.screen(uid)
                app.profiles.set_stage(uid, 'city')
                await app.role_cmd(self.command(uid))
                mid = app.screen_id(uid)
                self.assertNotEqual(mid, old_mid)
                self.assertEqual(app.profiles.stage(uid), '')
                buttons = self.transport.send_message.call_args.kwargs['attachments'][0].payload.buttons
                self.assertTrue(buttons[0][0].payload.endswith(':role:select'))
                await app.callback(self.click(uid, mid, 'role:select'))
                self.assertEqual(app.profiles.stage(uid), 'role_change_reason')
                event = NS(message=NS(sender=NS(user_id=uid), body=NS(text='Хочу сменить роль')))
                await app.text_handler(event)
                request = app.rr.pending_for_user(uid)
                self.assertEqual(request['from_role'], role)
                self.assertEqual(request['to_role'], 'teacher' if role == 'student' else 'student')
                self.assertEqual(app.profiles.get(uid)['role'], role)
                await app.role_cmd(self.command(uid))
                self.assertIn('уже на рассмотрении', self.transport.send_message.call_args.kwargs['text'])
                self.assertEqual(app.rr.pending_for_user(uid)['id'], request['id'])

    async def test_role_command_new_user(self):
        await app.role_cmd(self.command())
        self.assertIn('🎯 Место встречи изменить нельзя.', self.transport.send_message.call_args.kwargs['text'])
        self.assertIsNone(app.rr.pending_for_user(1))

    async def test_role_change_not_in_main_menu(self):
        for uid, role in [(1, 'student'), (2, 'teacher')]:
            app.profiles.choose(uid, role)
            _, attachments = app.menu(uid)
            buttons = [button for row in attachments[0].payload.buttons for button in row]
            self.assertFalse(any(button.payload == 'role:change' for button in buttons))
