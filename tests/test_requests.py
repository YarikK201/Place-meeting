from test_bot import NavigationCase, app, catalog


class RequestNavigationTests(NavigationCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        app.profiles.choose(1, 'student', 'Первый')
        app.profiles.choose(2, 'student', 'Второй')

    def pending(self, sender, recipient):
        with catalog.connect() as db:
            db.execute("INSERT INTO friendship_requests VALUES (?,?,'pending','2026-11-08')",
                       (sender, recipient))

    async def open_menu_requests(self):
        text, attachments = app.menu(1)
        await app.show_screen(1, text, attachments, force_new=True)
        args = self.transport.send_message.call_args.kwargs
        button = next(b for row in args['attachments'][0].payload.buttons
                      for b in row if b.text == '📩 Заявки')
        event = self.click(1, app.screen_id(1))
        event.callback.payload = button.payload
        await app.callback(event)
        return self.transport.edit_message.call_args.kwargs

    async def test_menu_opens_outgoing_when_no_incoming(self):
        self.pending(1, 2)
        result = await self.open_menu_requests()
        self.assertIn('Второй', result['text'])
        buttons = [b for row in result['attachments'][0].payload.buttons for b in row]
        self.assertTrue(any(b.payload.endswith(':req:cancel:2') for b in buttons))
        self.assertFalse(any(':req:accept:' in b.payload for b in buttons))

    async def test_menu_opens_incoming(self):
        self.pending(2, 1)
        result = await self.open_menu_requests()
        self.assertIn('Второй', result['text'])
        buttons = [b for row in result['attachments'][0].payload.buttons for b in row]
        self.assertTrue(any(b.payload.endswith(':req:accept:2') for b in buttons))

    async def test_both_directions_can_be_opened(self):
        self.pending(1, 2)
        self.pending(2, 1)
        result = await self.open_menu_requests()
        buttons = [b for row in result['attachments'][0].payload.buttons for b in row]
        self.assertTrue(any(b.payload.endswith(':req:out:0') for b in buttons))
        await app.callback(self.click(1, app.screen_id(1), 'req:out:0'))
        result = self.transport.edit_message.call_args.kwargs
        buttons = [b for row in result['attachments'][0].payload.buttons for b in row]
        self.assertTrue(any(b.payload.endswith(':req:cancel:2') for b in buttons))

    async def test_empty_and_finished_requests_do_not_recurse(self):
        for direction in ('in', 'out'):
            self.assertIn('Активных заявок пока нет', app.req_screen(1, direction)[0])
        self.pending(1, 2)
        app.social.cancel(1, 2)
        for direction in ('in', 'out'):
            self.assertIn('Активных заявок пока нет', app.req_screen(1, direction)[0])

    async def test_empty_outgoing_falls_back_to_incoming(self):
        self.pending(2, 1)
        text, _ = await app.dispatch(1, ['req', 'out', '10'])
        self.assertIn('Заявка 1/1', text)
        self.assertIn('Второй', text)
