import ast
import base64
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
        self.mail = GmailDelivery("sender@example.org", "reader@example.org", "id", "secret", "refresh")
        self.mail.token = "test-token"
        self.message = blog.build_email(RESULT, self.mail.sender, self.mail.recipient, "<test@example.org>")

    @patch("delivery.requests.post")
    def test_utf8_mime_and_api_acceptance(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"id": "accepted"}
        self.assertEqual(self.mail.send(self.message), "accepted")
        raw = post.call_args.kwargs["json"]["raw"]
        decoded = message_from_bytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(decoded["Message-ID"], "<test@example.org>")
        self.assertIn("한글 초안", decoded.get_payload()[0].get_payload(decode=True).decode("utf-8"))
        self.assertEqual(post.call_count, 1)
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    @patch("delivery.requests.post")
    def test_rejections_vs_uncertain_responses_no_http_retries(self, post):
        for code in (400, 401, 403, 404, 413, 429, 500, 502, 503, 302):
            with self.subTest(code=code):
                post.reset_mock()
                post.return_value = Mock(status_code=code)
                expected = RejectedDelivery if code in (400, 401, 403, 404, 413, 429) else UncertainDelivery
                with self.assertRaises(expected):
                    self.mail.send(self.message)
                self.assertEqual(post.call_count, 1)

    @patch("delivery.requests.post", side_effect=requests.Timeout("sensitive body"))
    def test_lost_response_is_sanitized(self, post):
        with self.assertRaises(UncertainDelivery) as error:
            self.mail.send(self.message)
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(post.call_count, 1)

    @patch("delivery.requests.post")
    def test_success_without_id_is_uncertain(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {}
        with self.assertRaises(UncertainDelivery):
            self.mail.send(self.message)

    @patch("delivery.requests.post")
    def test_oauth_rejection_does_not_leak_body(self, post):
        post.return_value = Mock(status_code=400, text="secret refresh-token")
        with self.assertRaises(DeliveryError) as error:
            self.mail.authenticate()
        self.assertNotIn("secret", str(error.exception))


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
