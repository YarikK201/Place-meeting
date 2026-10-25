"""MAX bot with private event catalog. Python stdlib; TLS verification enabled."""
import json
import logging
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from catalog import connect, count, one

API_URL = 'https://platform-api2.max.ru'
LOG = logging.getLogger('place_meeting')
WELCOME = 'Привет! Это «Место встречи». Нажми «Каталог» или введи /catalog.'


class MaxAPIError(Exception):
    pass


class MaxClient:
    def __init__(self, token):
        if not token.strip():
            raise ValueError('MAX_BOT_TOKEN is empty')
        self.token = token

    def request(self, method, endpoint, *, params=None, payload=None, timeout=40):
        url = API_URL + endpoint
        if params:
            url += '?' + urlencode(params)
        headers = {'Authorization': self.token, 'Accept': 'application/json'}
        body = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        try:
            with urlopen(Request(url, headers=headers, data=body, method=method), timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            raise MaxAPIError(f'MAX API HTTP {exc.code} on {endpoint}') from None
        except (URLError, TimeoutError) as exc:
            raise MaxAPIError(f'MAX API connection failed on {endpoint}: {type(exc).__name__}') from None

    def me(self):
        return self.request('GET', '/me')

    def subscriptions(self):
        return self.request('GET', '/subscriptions')

    def updates(self, marker=None):
        p = {'timeout': 30, 'limit': 100, 'types': 'message_created,message_callback'}
        if marker is not None:
            p['marker'] = marker
        return self.request('GET', '/updates', params=p)

    def send_to_user(self, user_id, text, buttons=None):
        payload = message_body(text, buttons)
        return self.request('POST', '/messages', params={'user_id': user_id}, payload=payload)

    def answer_callback(self, callback_id, text, buttons=None):
        return self.request('POST', '/answers', params={'callback_id': callback_id},
                            payload={'message': message_body(text, buttons)})


def message_body(text, buttons=None):
    payload = {'text': text[:4000]}
    if buttons:
        payload['attachments'] = [{'type': 'inline_keyboard', 'payload': {'buttons': buttons}}]
    return payload


def callback_button(text, payload):
    return {'type': 'callback', 'text': text, 'payload': payload}


def keyboard_for(index, total, url, detailed=False):
    navigation = []
    if index > 0:
        navigation.append(callback_button('◀ Назад', f'cat:{index-1}:0'))
    if index + 1 < total:
        navigation.append(callback_button('Далее ▶', f'cat:{index+1}:0'))
    rows = []
    if navigation:
        rows.append(navigation)
    if not detailed:
        rows.append([callback_button('Подробнее', f'cat:{index}:1')])
    if url:
        rows.append([{'type': 'link', 'text': 'Первоисточник ↗', 'url': url}])
    rows.append([callback_button('К началу', 'cat:0:0')])
    return rows


def catalog_card(db, index, detailed):
    total = count(db)
    if not total:
        return ('В каталоге пока нет мероприятий. Загляни позже!', None)
    index = max(0, min(index, total - 1))
    row = one(db, index)
    text = f'Мероприятие {index+1} из {total}\n\n{row["title"]}'
    if row['event_date']:
        text += f'\nДата: {row["event_date"]}'
    if row['location']:
        text += f'\nМесто: {row["location"]}'
    if detailed:
        text += f'\n\n{row["description"] or "Описание пока не добавлено."}'
    return text[:4000], keyboard_for(index, total, row['source_url'], detailed)


def reply_target(update):
    """Legacy handler used by the original project tests."""
    if update.get('update_type') != 'message_created':
        return None
    message = update.get('message') or {}
    if (message.get('recipient') or {}).get('chat_type') != 'dialog':
        return None
    cmd = ((message.get('body') or {}).get('text') or '').strip().split(maxsplit=1)
    if not cmd or cmd[0].split('@')[0] != '/start':
        return None
    return (message.get('sender') or {}).get('user_id')


def process_update(client, db, update):
    kind = update.get('update_type')
    if kind == 'message_created':
        message = update.get('message') or {}
        if (message.get('recipient') or {}).get('chat_type') != 'dialog':
            return False
        user_id = (message.get('sender') or {}).get('user_id')
        command = ((message.get('body') or {}).get('text') or '').strip().split(maxsplit=1)
        if not user_id or not command:
            return False
        cmd = command[0].split('@')[0].lower()
        if cmd == '/start':
            client.send_to_user(user_id, WELCOME, [[callback_button('Каталог', 'cat:0:0')]])
        elif cmd in ('/catalog', '/events'):
            text, buttons = catalog_card(db, 0, False)
            client.send_to_user(user_id, text, buttons)
        else:
            return False
        return True
    if kind == 'message_callback':
        callback = update.get('callback') or {}
        payload = str(callback.get('payload') or '')
        callback_id = callback.get('callback_id')
        message = update.get('message') or {}
        if (message.get('recipient') or {}).get('chat_type') != 'dialog':
            return False
        if not callback_id or not payload.startswith('cat:'):
            return False
        parts = payload.split(':')
        if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ('0', '1'):
            return False
        text, buttons = catalog_card(db, int(parts[1]), parts[2] == '1')
        client.answer_callback(callback_id, text, buttons)
        return True
    return False


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    token = os.environ.get('MAX_BOT_TOKEN', '')
    if not token:
        raise SystemExit('Set MAX_BOT_TOKEN environment variable (do not commit it).')
    client = MaxClient(token)
    LOG.info('Authenticated MAX bot: user_id=%s', client.me().get('user_id'))
    if os.environ.get('MAX_BOT_RUN') != '1':
        LOG.info('Verification only; set MAX_BOT_RUN=1 to enable polling.')
        return
    if client.subscriptions().get('subscriptions'):
        raise SystemExit('Webhook subscription exists: polling cannot be used concurrently.')
    with connect() as db:
        LOG.info('Catalog has %s events', count(db))
        marker = None
        LOG.info('Starting local Long Polling test mode. Stop with Ctrl+C.')
        while True:
            try:
                batch = client.updates(marker)
                for update in batch.get('updates', []):
                    try:
                        process_update(client, db, update)
                    except MaxAPIError:
                        LOG.exception('MAX API error processing update')
                marker = batch.get('marker', marker)
            except MaxAPIError as exc:
                LOG.warning('%s; retrying in 5 seconds', exc)
                time.sleep(5)


if __name__ == '__main__':
    main()
