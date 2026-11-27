"""Offline regression tests: no real OpenAI, HTTP, or Gmail calls."""
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import fetch_and_write as blog


class FakeResponse:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


class BlogTests(unittest.TestCase):
    def test_model_routing(self):
        self.assertEqual(blog.SELECTION_MODEL, "gpt-6.1-terra")
        self.assertEqual(blog.WRITING_MODEL, "gemini-4-neon")
        self.assertEqual(blog.MODEL_PRICING[blog.SELECTION_MODEL]["output"], 5.0)
        self.assertEqual(blog.MODEL_PRICING[blog.WRITING_MODEL]["output"], 15.0)

    def test_article_selection_uses_terra_no_reasoning(self):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text="1", usage=None)
        article = {"title": "Handling Stress", "desc": "Psychology Today psychology article"}
        chosen = blog.rank_articles(client, [article])
        self.assertEqual(chosen, [0])
        args = client.responses.create.call_args.kwargs
        self.assertEqual(args["model"], blog.SELECTION_MODEL)
        self.assertEqual(args["reasoning"], {"effort": "none"})

    def test_essay_writing_uses_gemini_neon(self):
        client = MagicMock()
        response = MagicMock()
        response.json.return_value = {
            "candidates": [{"content": {"parts": [
                {"text": "internal reasoning", "thought": True},
                {"text": "차분한 마음의 이야기\n\n본문입니다."}
            ]}}],
            "usageMetadata": {
                "promptTokenCount": 1000, "cachedContentTokenCount": 100,
                "candidatesTokenCount": 250, "thoughtsTokenCount": 80
            }
        }
        with patch.dict(blog.os.environ, {"GEMINI_API_KEY": "test-key"}), patch.dict(
            blog.TEXT_USAGE, {blog.WRITING_MODEL: {"input": 0, "output": 0, "cached_input": 0, "calls": 0}}
        ), patch.object(blog.requests, "post", return_value=response) as post:
            result = blog.call_writer(client, "예시 기사")
            self.assertTrue(result.startswith("차분한"))
            self.assertNotIn("internal reasoning", result)
            self.assertEqual(blog.TEXT_USAGE[blog.WRITING_MODEL], {
                "input": 1000, "output": 330, "cached_input": 100, "calls": 1
            })
        self.assertTrue(post.call_args.args[0].endswith("/gemini-4-neon:generateContent"))
        args = post.call_args.kwargs
        self.assertEqual(args["headers"]["x-goog-api-key"], "test-key")
        self.assertIn("심리학 에세이", args["json"]["systemInstruction"]["parts"][0]["text"])
        self.assertEqual(args["json"]["contents"][0]["parts"][0]["text"], "예시 기사")
        client.responses.create.assert_not_called()

    def test_gemini_retries_after_token_exhaustion(self):
        first, second = MagicMock(), MagicMock()
        first.json.return_value = {
            "candidates": [{"finishReason": "MAX_TOKENS"}],
            "usageMetadata": {"thoughtsTokenCount": 16000}
        }
        second.json.return_value = {
            "candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": "완성된 원고"}]}}],
            "usageMetadata": {"candidatesTokenCount": 250}
        }
        with patch.dict(blog.os.environ, {"GEMINI_API_KEY": "test-key"}), patch.dict(
            blog.TEXT_USAGE, {blog.WRITING_MODEL: {"input": 0, "output": 0, "cached_input": 0, "calls": 0}}
        ), patch.object(blog.requests, "post", side_effect=[first, second]) as post:
            self.assertEqual(blog.call_writer(MagicMock(), "예시 기사"), "완성된 원고")
            self.assertEqual(blog.TEXT_USAGE[blog.WRITING_MODEL]["calls"], 2)
            self.assertEqual(blog.TEXT_USAGE[blog.WRITING_MODEL]["output"], 16250)
        configs = [call.kwargs["json"]["generationConfig"] for call in post.call_args_list]
        self.assertEqual(configs[0]["thinkingConfig"]["thinkingLevel"], "MEDIUM")
        self.assertEqual(configs[1]["thinkingConfig"]["thinkingLevel"], "LOW")
        self.assertEqual(configs[0]["maxOutputTokens"], 16384)
        self.assertEqual(configs[1]["maxOutputTokens"], 32768)

    def test_gemini_does_not_retry_safety_block(self):
        response = MagicMock()
        response.json.return_value = {"promptFeedback": {"blockReason": "SAFETY"}}
        with patch.dict(blog.os.environ, {"GEMINI_API_KEY": "test-key"}), patch.dict(
            blog.TEXT_USAGE, {blog.WRITING_MODEL: {"input": 0, "output": 0, "cached_input": 0, "calls": 0}}
        ), patch.object(blog.requests, "post", return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, "SAFETY"):
                blog.call_writer(MagicMock(), "예시 기사")
            post.assert_called_once()

    def test_gemini_key_is_required(self):
        with patch.dict(blog.os.environ, {"GEMINI_API_KEY": ""}), patch.object(blog.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                blog.call_writer(MagicMock(), "예시 기사")
            post.assert_not_called()

    def test_usage_is_tracked_by_model(self):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=250, input_tokens_details=SimpleNamespace(cached_tokens=100))
        record = SimpleNamespace(usage=usage)
        with patch.dict(blog.TEXT_USAGE, {blog.SELECTION_MODEL: {"input": 0, "output": 0, "cached_input": 0, "calls": 0}}):
            blog.record_text_usage(record, blog.SELECTION_MODEL)
            self.assertEqual(blog.TEXT_USAGE[blog.SELECTION_MODEL]["input"], 1000)
            self.assertEqual(blog.TEXT_USAGE[blog.SELECTION_MODEL]["calls"], 1)

    def test_pt_url_restriction(self):
        self.assertTrue(blog.is_pt_url("https://www.psychologytoday.com/us/blog/example"))
        self.assertFalse(blog.is_pt_url("https://psychologytoday.com.evil.example/post"))
        self.assertFalse(blog.is_pt_url("http://www.psychologytoday.com/us/blog/example"))

    def test_article_extracts_content(self):
        html = "<html><article>" + "".join(
            "<p>" + ("Useful psychological explanation with specific context. " * 5) + str(i) + "</p>"
            for i in range(4)
        ) + "</article></html>"
        with patch.object(blog.requests, "get", return_value=FakeResponse(text=html)):
            extracted = blog.fetch_article_content("https://www.psychologytoday.com/us/blog/test")
        self.assertGreaterEqual(len(extracted), 650)

    def test_short_article_not_used(self):
        with patch.object(blog.requests, "get", return_value=FakeResponse(text="<article><p>Short excerpt.</p></article>")):
            with self.assertRaises(ValueError):
                blog.fetch_article_content("https://www.psychologytoday.com/us/blog/test")

    def test_valid_plain_draft(self):
        title = "떠나는 리더가 조직에 남기는 마지막 질문"
        paragraph = "리더가 조직을 떠나는 순간에는 개인의 결정보다 남겨진 관계의 변화가 더 중요할 수 있습니다. "
        draft = title + "\n\n" + (paragraph * 20)
        self.assertEqual(blog.draft_issues(draft), [])

    def test_new_length_limits(self):
        self.assertEqual(blog.MIN_BLOG_CHARS, 900)
        self.assertEqual(blog.MAX_BLOG_CHARS, 1200)
        title = "떠나는 리더가 조직에 남기는 마지막 질문"
        short = title + "\n\n" + "심리학 설명입니다. " * 50
        long = title + "\n\n" + "심리학 설명입니다. " * 250
        self.assertTrue(any("900" in s for s in blog.draft_issues(short)))
        self.assertTrue(any("1200" in s for s in blog.draft_issues(long)))

    def test_email_is_plain_text_and_has_no_attachments(self):
        article = {"title": "Psychology Today Article", "url": "https://www.psychologytoday.com/us/blog/test"}
        with patch.dict(blog.os.environ, {
            "GMAIL_ADDRESS": "sender@example.com",
            "GMAIL_APP_PASSWORD": "testpassword",
            "RECIPIENT_EMAIL": "recipient@example.com",
        }):
            with patch.object(blog.smtplib, "SMTP") as smtp:
                blog.send_email(article, "심리학 설명\n\n심리학 칼럼 본문")
                msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertFalse(msg.is_multipart())
        self.assertIn("심리학 칼럼 본문", msg.get_payload(decode=True).decode("utf-8"))
        self.assertEqual(msg.get_content_charset(), "utf-8")

    def test_image_generation_is_removed(self):
        self.assertFalse(hasattr(blog, "generate_cover_image"))
        self.assertFalse(hasattr(blog, "IMAGE_MODEL"))

    def test_markdown_is_rejected(self):
        draft = "# 제목\n\n" + ("상담심리학에 대한 설명입니다. " * 130)
        self.assertTrue(any("Markdown" in issue for issue in blog.draft_issues(draft)))

    def test_email_body_is_plain_text_without_claude(self):
        article = {"title": "A Psychology Today Article", "url": "https://www.psychologytoday.com/us/blog/test"}
        body = blog.compose_email(article, "심리학적 질문\n\n독립적인 해설 본문입니다.")
        self.assertIn("출처: Psychology Today", body)
        self.assertIn("원문 URL: https://www.psychologytoday.com", body)
        self.assertNotIn("Claude", body)
        self.assertIn("Gemini 4 Neon", body)
        self.assertNotIn("<html>", body)

    def test_only_one_main_guard(self):
        with open(blog.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(source.count('if __name__ == "__main__":'), 1)


if __name__ == "__main__":
    unittest.main()
