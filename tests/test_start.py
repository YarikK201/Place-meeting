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
        self.assertIn('Привет',args['text'])
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
        self.assertEqual(self.transport.send_message.call_args.kwargs['text'],'🏠 Главное меню')
        await app.start(self.command())
        self.assertEqual(self.transport.edit_message.call_args.kwargs['text'],'🏠 Главное меню')

    async def test_start_command_new_user_and_system_event(self):
        await app.start(self.command())
        await app.bot_started(self.event())
        self.transport.send_message.assert_awaited_once()
        self.assertIn('Привет',self.transport.edit_message.call_args.kwargs['text'])

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
