"""Offline regression tests: no real OpenAI, HTTP, or Gmail calls."""
import unittest
from unittest.mock import patch

import fetch_and_write as blog


class FakeResponse:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


class BlogTests(unittest.TestCase):
    def test_model_is_gpt_only(self):
        self.assertEqual(blog.MODEL, "gpt-6.1-terra")

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
        self.assertNotIn("<html>", body)

    def test_only_one_main_guard(self):
        with open(blog.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(source.count('if __name__ == "__main__":'), 1)


if __name__ == "__main__":
    unittest.main()
