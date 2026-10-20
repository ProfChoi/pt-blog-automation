import ast
import smtplib
import copy
from email import message_from_bytes
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from delivery import (
    DeliveryError, DeliveryStore, GmailDelivery, RejectedDelivery,
    UncertainDelivery, run_delivery,
)
import fetch_and_write as blog


ARTICLE = {"title": "Test article", "url": "https://example.org/article", "desc": "Summary"}
RESULT = {**ARTICLE, "blog_content": "한글 초안"}
TODAY = "2026-10-20"


class MemoryStore:
    def __init__(self):
        self.state = {"version": 1, "deliveries": {}}
        self.remote = copy.deepcopy(self.state)
        self.used_urls = set()
        self.checkpoints = 0
        self.fail_at = None

    def used(self):
        return self.used_urls

    def checkpoint(self):
        self.checkpoints += 1
        if self.checkpoints == self.fail_at:
            raise DeliveryError("Simulated push failure")
        self.remote = copy.deepcopy(self.state)
        self.used_urls.update(row["article"]["url"] for row in self.state["deliveries"].values()
                              if row["status"] == "sent")

    def restart(self):
        self.state = copy.deepcopy(self.remote)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.mail = Mock(sender="sender@example.org", recipient="reader@example.org")
        self.mail.send.return_value = "gmail-123"
        self.fetch = Mock(return_value=[ARTICLE])
        self.write = Mock(return_value=RESULT)

    def run_pipeline(self, date=TODAY):
        run_delivery(self.store, self.mail, self.fetch, self.write, blog.build_email, date)

    def test_success_marks_used_only_after_send_and_skips_rerun(self):
        def send(message):
            self.assertFalse(self.store.used())
            self.assertEqual(self.store.remote["deliveries"][TODAY]["status"], "sending")
            return "gmail-123"
        self.mail.send.side_effect = send
        self.run_pipeline()
        self.assertEqual(self.store.used(), {ARTICLE["url"]})
        self.assertEqual(self.store.remote["deliveries"][TODAY]["status"], "sent")
        self.run_pipeline()
        self.assertEqual(self.mail.send.call_count, 1)
        self.assertEqual(self.write.call_count, 1)

    def test_explicit_rejection_retries_same_article(self):
        self.mail.send.side_effect = RejectedDelivery("403")
        with self.assertRaises(RejectedDelivery):
            self.run_pipeline()
        self.assertFalse(self.store.used())
        self.assertEqual(self.store.remote["deliveries"][TODAY]["status"], "prepared")
        self.store.restart()
        self.mail.send.side_effect = None
        self.fetch.reset_mock()
        self.run_pipeline()
        self.fetch.assert_not_called()
        self.write.assert_called_with([ARTICLE])

    def test_timeout_blocks_next_day_and_does_not_mark_used(self):
        self.mail.send.side_effect = UncertainDelivery("timeout")
        with self.assertRaises(UncertainDelivery):
            self.run_pipeline()
        self.store.restart()
        with self.assertRaises(UncertainDelivery):
            self.run_pipeline("2026-10-21")
        self.assertEqual(self.mail.send.call_count, 1)
        self.assertFalse(self.store.used())

    def test_pre_send_push_failures_never_send(self):
        for checkpoint in (1, 2):
            with self.subTest(checkpoint=checkpoint):
                self.setUp()
                self.store.fail_at = checkpoint
                with self.assertRaises(DeliveryError):
                    self.run_pipeline()
                self.mail.send.assert_not_called()
                self.assertFalse(self.store.used())

    def test_post_send_push_failure_blocks_resend(self):
        self.store.fail_at = 3
        with self.assertRaises(DeliveryError):
            self.run_pipeline()
        self.assertEqual(self.store.remote["deliveries"][TODAY]["status"], "sending")
        self.store.restart()
        with self.assertRaises(UncertainDelivery):
            self.run_pipeline()
        self.assertEqual(self.mail.send.call_count, 1)

    def test_auth_failure_precedes_generation(self):
        self.mail.authenticate.side_effect = DeliveryError("expired grant")
        with self.assertRaises(DeliveryError):
            self.run_pipeline()
        self.fetch.assert_not_called()
        self.write.assert_not_called()
        self.mail.send.assert_not_called()
        self.assertFalse(self.store.used())

    def test_no_new_article_does_not_send(self):
        self.store.used_urls.add(ARTICLE["url"])
        self.run_pipeline()
        self.write.assert_not_called()
        self.mail.send.assert_not_called()

    def test_drafting_failure_does_not_mark_used(self):
        self.write.side_effect = RuntimeError("Claude unavailable")
        with self.assertRaises(RuntimeError):
            self.run_pipeline()
        self.mail.send.assert_not_called()
        self.assertFalse(self.store.used())

    def test_old_pending_delivery_counts_as_todays_send(self):
        self.mail.send.side_effect = RejectedDelivery("429")
        with self.assertRaises(RejectedDelivery):
            self.run_pipeline()
        self.mail.send.side_effect = None
        self.run_pipeline("2026-10-21")
        self.run_pipeline("2026-10-21")
        self.assertEqual(self.mail.send.call_count, 2)  # one rejected, one accepted

    def test_operator_confirmed_send_repairs_used_history_without_send(self):
        self.mail.send.side_effect = UncertainDelivery("timeout")
        with self.assertRaises(UncertainDelivery):
            self.run_pipeline()
        self.store.state["deliveries"][TODAY]["status"] = "sent"
        self.run_pipeline()
        self.assertIn(ARTICLE["url"], self.store.used())
        self.assertEqual(self.mail.send.call_count, 1)


class GmailTests(unittest.TestCase):
    def setUp(self):
        self.mail = GmailDelivery("sender@example.org", "reader@example.org", "abcd efgh ijkl mnop")
        self.message = blog.build_email(RESULT, self.mail.sender, self.mail.recipient, "<test@example.org>")

    @patch("delivery.smtplib.SMTP")
    def test_utf8_mime_and_smtp_acceptance(self, smtp):
        server = smtp.return_value
        server.sendmail.return_value = {}
        self.assertEqual(self.mail.send(self.message), "smtp-accepted")
        decoded = message_from_bytes(server.sendmail.call_args.args[2])
        self.assertEqual(decoded["Message-ID"], "<test@example.org>")
        self.assertIn("한글 초안", decoded.get_payload()[0].get_payload(decode=True).decode("utf-8"))
        server.login.assert_called_once_with(self.mail.sender, "abcdefghijklmnop", initial_response_ok=False)
        server.starttls.assert_called_once()
        server.quit.assert_called_once()

    @patch("delivery.smtplib.SMTP")
    def test_authentication_probe_sends_no_message(self, smtp):
        self.mail.authenticate()
        smtp.return_value.sendmail.assert_not_called()
        smtp.return_value.quit.assert_called_once()

    @patch("delivery.smtplib.SMTP_SSL")
    @patch("delivery.smtplib.SMTP")
    def test_login_disconnect_fallback_before_send(self, smtp, ssl_smtp):
        smtp.return_value.login.side_effect = smtplib.SMTPServerDisconnected("private detail")
        ssl_smtp.return_value.sendmail.return_value = {}
        self.assertEqual(self.mail.send(self.message), "smtp-accepted")
        smtp.return_value.sendmail.assert_not_called()
        ssl_smtp.return_value.sendmail.assert_called_once()

    @patch("delivery.smtplib.SMTP_SSL")
    @patch("delivery.smtplib.SMTP")
    def test_password_rejection_does_not_try_another_port(self, smtp, ssl_smtp):
        smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private detail")
        with self.assertRaises(RejectedDelivery) as error:
            self.mail.send(self.message)
        self.assertNotIn("private", str(error.exception))
        ssl_smtp.assert_not_called()
        smtp.return_value.sendmail.assert_not_called()

    @patch("delivery.smtplib.SMTP_SSL")
    @patch("delivery.smtplib.SMTP")
    def test_send_disconnect_is_uncertain_and_never_falls_back(self, smtp, ssl_smtp):
        smtp.return_value.sendmail.side_effect = smtplib.SMTPServerDisconnected("private detail")
        with self.assertRaises(UncertainDelivery) as error:
            self.mail.send(self.message)
        self.assertNotIn("private", str(error.exception))
        ssl_smtp.assert_not_called()
        smtp.return_value.sendmail.assert_called_once()

    @patch("delivery.smtplib.SMTP")
    def test_explicit_data_rejection_is_retryable(self, smtp):
        smtp.return_value.sendmail.side_effect = smtplib.SMTPDataError(550, b"rejected")
        with self.assertRaises(RejectedDelivery):
            self.mail.send(self.message)

    @patch("delivery.smtplib.SMTP")
    def test_quit_disconnect_after_success_remains_success(self, smtp):
        smtp.return_value.sendmail.return_value = {}
        smtp.return_value.quit.side_effect = smtplib.SMTPServerDisconnected("closed")
        self.assertEqual(self.mail.send(self.message), "smtp-accepted")
        smtp.return_value.close.assert_called_once()

    @patch("delivery.smtplib.SMTP_SSL")
    @patch("delivery.smtplib.SMTP")
    def test_both_ports_fail_before_send(self, smtp, ssl_smtp):
        smtp.return_value.login.side_effect = smtplib.SMTPServerDisconnected("closed")
        ssl_smtp.return_value.login.side_effect = smtplib.SMTPServerDisconnected("closed")
        with self.assertRaises(RejectedDelivery):
            self.mail.authenticate()
        smtp.return_value.sendmail.assert_not_called()
        ssl_smtp.return_value.sendmail.assert_not_called()


class SourceTests(unittest.TestCase):
    def test_one_main_entrypoint(self):
        tree = ast.parse(Path(blog.__file__).read_text(encoding="utf-8"))
        guards = [node for node in tree.body if isinstance(node, ast.If)
                  and "__main__" in ast.unparse(node.test)]
        self.assertEqual(len(guards), 1)

    @patch("fetch_and_write.requests.get")
    def test_rss_http_error_is_not_parsed_as_no_articles(self, get):
        get.return_value.raise_for_status.side_effect = requests.HTTPError()
        with self.assertRaises(requests.HTTPError):
            blog.fetch_pt_articles()

    @patch("fetch_and_write.anthropic.Anthropic")
    @patch("fetch_and_write.fetch_article_content", return_value="Original text")
    @patch.dict("os.environ", {"CLAUDE_API_KEY": "test"})
    def test_existing_selection_and_writing_contract(self, fetch, anthropic):
        client = anthropic.return_value
        client.messages.create.side_effect = [
            Mock(content=[Mock(text="2")]), Mock(content=[Mock(text="Draft")]),
        ]
        second = {**ARTICLE, "title": "Second", "url": "https://example.org/second"}
        result = blog.select_and_write_blog([ARTICLE, second])
        self.assertEqual(result["url"], second["url"])
        calls = client.messages.create.call_args_list
        self.assertEqual(calls[0].kwargs["model"], "claude-sonnet-4-6")
        self.assertEqual(calls[0].kwargs["max_tokens"], 10)
        self.assertEqual(calls[1].kwargs["max_tokens"], 3000)
        prompt = calls[1].kwargs["messages"][0]["content"]
        self.assertIn(blog.STYLE_SAMPLES, prompt)
        self.assertIn("1200~1500자", prompt)
        self.assertIn("Original text", prompt)

    def test_corrupt_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery_state.json"
            path.write_text('{"version": 9, "deliveries": {}}', encoding="utf-8")
            with patch.object(DeliveryStore, "state_path", path):
                with self.assertRaises(DeliveryError):
                    DeliveryStore()


if __name__ == "__main__":
    unittest.main()
