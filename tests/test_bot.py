import unittest
from unittest.mock import patch, MagicMock
from bot import MaxClient, reply_target, WELCOME


class BotTests(unittest.TestCase):
    def test_start_in_private_dialog(self):
        update = {"update_type": "message_created", "message": {
            "body": {"text": "/start"}, "recipient": {"chat_type": "dialog"},
            "sender": {"user_id": 12345}}}
        self.assertEqual(reply_target(update), 12345)

    def test_ignore_group_and_other_commands(self):
        base = {"update_type": "message_created", "message": {
            "body": {"text": "/start"}, "recipient": {"chat_type": "chat"},
            "sender": {"user_id": 12345}}}
        self.assertIsNone(reply_target(base))
        base["message"]["recipient"]["chat_type"] = "dialog"
        base["message"]["body"]["text"] = "/other"
        self.assertIsNone(reply_target(base))

    def test_authorization_header_not_url(self):
        client = MaxClient("dummy-secret")
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"user_id": 123}'
        with patch("bot.urlopen", return_value=response) as mocked:
            client.me()
            req = mocked.call_args.args[0]
            self.assertEqual(req.get_header("Authorization"), "dummy-secret")
            self.assertNotIn("dummy-secret", req.full_url)

    def test_welcome(self):
        self.assertIn("Место встречи", WELCOME)


if __name__ == "__main__":
    unittest.main()
