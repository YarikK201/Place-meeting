"""Offline MAX navigation tests: real SQLite and maxapi buttons, stub transport."""
import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

# Import-time initialization must never touch a real database or token.
_TEMP = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {'MAX_BOT_TOKEN': 'offline-test', 'EVENTS_DB': _TEMP.name + '/db.sqlite'}):
    import catalog
    catalog.PATH = _TEMP.name + '/db.sqlite'
    import bot as app

from maxapi.exceptions import MaxApiError


def message(mid):
    return NS(body=NS(mid=mid))


class NavigationCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with catalog.connect() as db:
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for row in tables:
                if not row['name'].startswith('sqlite_'):
                    db.execute('DELETE FROM "' + row['name'] + '"')
        app.flt.invalidate_events()
        app._ACTION_LOCKS.clear()
        app._SCREEN_LOCKS.clear()
        self.sequence = 0
        self.transport = NS(send_message=AsyncMock(side_effect=self.send),
                            edit_message=AsyncMock(return_value=NS(success=True)),
                            delete_message=AsyncMock(return_value=NS(success=True)))
        self.bot_patch = patch.object(app, 'bot', self.transport)
        self.bot_patch.start()
        self.sleep_patch = patch.object(app.asyncio, 'sleep', new=AsyncMock())
        self.sleep_patch.start()

    async def send(self, **kwargs):
        self.sequence += 1
        return NS(message=message(f'm{self.sequence}'))

    async def asyncTearDown(self):
        tasks = list(app._CLEANUP_TASKS.values()) + list(app._NOTIFY_TASKS)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        app._CLEANUP_TASKS.clear()
        app._NOTIFY_TASKS.clear()
        self.sleep_patch.stop()
        self.bot_patch.stop()

    async def screen(self, uid=1, action='home'):
        await app.show_screen(uid, 'screen', app.keyboard([[app.b('Go', action)]]), force_new=True)
        return app.screen_id(uid)

    def click(self, uid, mid, action='home', revision=None):
        if revision is None:
            with catalog.connect() as db:
                row = db.execute('SELECT revision FROM ui_callbacks WHERE user_id=? AND message_id=?', (uid, mid)).fetchone()
                revision = row['revision'] if row else 'unknown'
        return NS(message=message(mid), callback=NS(user=NS(user_id=uid),
                  payload=f'ui:{revision}:{action}'), answer=AsyncMock())


class NavigationTests(NavigationCase):
    async def test_card_edits_clicked_message(self):
        mid = await self.screen()
        await app.callback(self.click(1, mid, 'card:0'))
        self.assertEqual(self.transport.edit_message.call_args.kwargs['message_id'], mid)
        self.assertEqual(self.transport.send_message.await_count, 1)

    async def test_new_screen_deletes_previous(self):
        old = await self.screen()
        await app.new_screen(1, 'next')
        self.assertNotEqual(app.screen_id(1), old)
        await app._CLEANUP_TASKS[1]
        self.transport.delete_message.assert_awaited_once_with(message_id=old)

    async def test_notification_edits_itself_and_removes_menu(self):
        old = await self.screen()
        await app.notify(1, 'notification', app.keyboard([[app.b('Open', 'home')]]))
        notification = 'm2'
        self.assertEqual(app.screen_id(1), old)
        await app.callback(self.click(1, notification))
        self.assertEqual(self.transport.edit_message.call_args.kwargs['message_id'], notification)
        self.assertEqual(app.screen_id(1), notification)
        await app._CLEANUP_TASKS[1]
        self.transport.delete_message.assert_awaited_once_with(message_id=old)

    async def test_fast_duplicate_toggles_once(self):
        mid = await self.screen()
        first = self.click(1, mid, 'favtoggle:5:card:0')
        second = self.click(1, mid, 'favtoggle:5:card:0')
        await asyncio.gather(app.callback(first), app.callback(second))
        self.assertTrue(app.fav.saved(1, 5))
        self.assertEqual(self.transport.edit_message.await_count, 1)
        first.answer.assert_awaited_once()
        second.answer.assert_awaited_once()

    async def test_other_users_progress_during_slow_edit(self):
        one, two = await self.screen(1), await self.screen(2)
        entered, release = asyncio.Event(), asyncio.Event()
        async def edit(**kwargs):
            if kwargs['message_id'] == one:
                entered.set()
                await release.wait()
            return NS(success=True)
        self.transport.edit_message.side_effect = edit
        task = asyncio.create_task(app.callback(self.click(1, one)))
        await entered.wait()
        await asyncio.wait_for(app.callback(self.click(2, two)), 1)
        self.assertFalse(task.done())
        release.set()
        await task
        self.assertEqual(app.screen_id(1), one)
        self.assertEqual(app.screen_id(2), two)

    async def test_stale_and_cross_user_callbacks_have_no_side_effect(self):
        old = await self.screen()
        stale = self.click(1, old, 'favtoggle:5:card:0')
        await self.screen()
        await app.callback(stale)
        await app.callback(self.click(2, app.screen_id(1), 'favtoggle:5:card:0'))
        self.assertFalse(app.fav.saved(1, 5))
        self.assertFalse(app.fav.saved(2, 5))
        self.transport.edit_message.assert_not_awaited()

    async def test_missing_message_and_legacy_button_are_ignored(self):
        event = self.click(1, None)
        event.message = None
        await app.callback(event)
        mid = await self.screen()
        event = self.click(1, mid)
        event.callback.payload = 'home'
        await app.callback(event)
        self.transport.edit_message.assert_not_awaited()

    async def test_api_failure_does_not_replay_business_action(self):
        mid = await self.screen()
        first = self.click(1, mid, 'favtoggle:5:card:0')
        self.transport.edit_message.side_effect = MaxApiError(503, 'private body')
        with self.assertLogs('place_meeting.ui', level='INFO') as captured:
            await app.callback(first)
            await app.callback(first)
        self.assertTrue(app.fav.saved(1, 5))
        self.assertEqual(self.transport.edit_message.await_count, 1)
        self.assertNotIn('private body', '\n'.join(captured.output))
        self.assertIn('/start', self.transport.send_message.call_args.kwargs['text'])

    async def test_404_replaces_missing_screen(self):
        mid = await self.screen()
        self.transport.edit_message.side_effect = MaxApiError(404, {})
        await app.callback(self.click(1, mid))
        self.assertNotEqual(app.screen_id(1), mid)
        self.assertEqual(self.transport.send_message.await_count, 2)

    async def test_false_success_is_failure(self):
        mid = await self.screen()
        self.transport.edit_message.return_value = NS(success=False)
        await app.callback(self.click(1, mid))
        self.assertEqual(app.screen_id(1), mid)
        self.assertIn('/start', self.transport.send_message.call_args.kwargs['text'])

    async def test_ack_precedes_dispatch_even_with_ack_failure(self):
        mid = await self.screen()
        event = self.click(1, mid)
        event.answer.side_effect = TimeoutError()
        async def dispatch(*args):
            event.answer.assert_awaited_once()
            return 'ok', []
        with patch.object(app, 'dispatch', side_effect=dispatch):
            await app.callback(event)
        self.transport.edit_message.assert_awaited_once()

    async def test_cleanup_does_not_block_next_transition(self):
        await self.screen()
        await app._CLEANUP_TASKS[1]
        entered, release = asyncio.Event(), asyncio.Event()
        async def delete(**kwargs):
            entered.set()
            await release.wait()
            return NS(success=True)
        self.transport.delete_message.side_effect = delete
        await app.new_screen(1, 'new')
        await entered.wait()
        await asyncio.wait_for(app.callback(self.click(1, app.screen_id(1))), 1)
        release.set()
        await app._CLEANUP_TASKS[1]

    async def test_failed_cleanup_keeps_id_for_retry(self):
        old = await self.screen()
        await app._CLEANUP_TASKS[1]
        self.transport.delete_message.side_effect = MaxApiError(429, {})
        await app.new_screen(1, 'new')
        await app._CLEANUP_TASKS[1]
        with catalog.connect() as db:
            self.assertEqual(db.execute('SELECT message_id FROM old_ui_messages').fetchone()[0], old)

    async def test_profile_text_creates_new_screen(self):
        app.profiles.choose(1, 'student')
        old = await self.screen()
        await app.callback(self.click(1, old, 'profile:field:city'))
        event = NS(message=NS(sender=NS(user_id=1), body=NS(text='Тестовый город')))
        await app.text_handler(event)
        self.assertEqual(app.profiles.get(1)['city'], 'Тестовый город')
        self.assertNotEqual(app.screen_id(1), old)

    async def test_teacher_verification_awaits_dispatch(self):
        app.profiles.choose(1, 'teacher')
        text, buttons = await app.dispatch(1, ['te','checkstart'])
        self.assertEqual(app.profiles.stage(1), 'teacher_full_name')
        self.assertTrue(buttons)

    async def test_versioned_buttons_do_not_mutate_shared_keyboard(self):
        buttons = app.keyboard([[app.b('Go', 'home')]])
        await app.show_screen(1, 'screen', buttons)
        self.assertEqual(buttons[0].payload.buttons[0][0].payload, 'home')
        sent = self.transport.send_message.call_args.kwargs['attachments']
        self.assertTrue(sent[0].payload.buttons[0][0].payload.startswith('ui:'))

    async def test_cleanup_collects_screens_retired_while_running(self):
        first = await self.screen()
        await app._CLEANUP_TASKS[1]
        entered, release = asyncio.Event(), asyncio.Event()
        async def delete(**kwargs):
            entered.set()
            await release.wait()
            return NS(success=True)
        self.transport.delete_message.side_effect = delete
        second = await self.screen()
        await entered.wait()
        third = await self.screen()
        release.set()
        await app._CLEANUP_TASKS[1]
        deleted = [call.kwargs['message_id'] for call in self.transport.delete_message.call_args_list]
        self.assertEqual(deleted, [first, second])
        self.assertNotIn(third, deleted)

    async def test_unrelated_notification_is_preserved(self):
        await self.screen()
        await app.notify(1, 'one', app.keyboard([[app.b('Go', 'home')]]))
        await app.notify(1, 'two', app.keyboard([[app.b('Go', 'home')]]))
        await app.callback(self.click(1, 'm2'))
        await app._CLEANUP_TASKS[1]
        self.transport.delete_message.assert_awaited_once_with(message_id='m1')
        await app.callback(self.click(1, 'm3'))
        self.assertEqual(app.screen_id(1), 'm3')

    async def test_revision_survives_in_memory_lock_reset(self):
        mid = await self.screen()
        event = self.click(1, mid)
        app._ACTION_LOCKS.clear()
        app._SCREEN_LOCKS.clear()
        await app.callback(event)
        self.transport.edit_message.assert_awaited_once()
