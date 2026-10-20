"""Gmail HTTPS delivery with durable, fail-closed send checkpoints."""
import base64
import json
import os
from pathlib import Path
import subprocess
import uuid

import requests


class DeliveryError(RuntimeError):
    pass


class RejectedDelivery(DeliveryError):
    """Google explicitly rejected the request; it may be retried later."""


class UncertainDelivery(DeliveryError):
    """The request might have been accepted. Never retry automatically."""


def required(name):
    value = os.environ.get(name, "").strip()
    if not value or "\r" in value or "\n" in value:
        raise DeliveryError(f"Missing or invalid configuration: {name}")
    return value


class GmailDelivery:
    def __init__(self, sender, recipient, client_id, client_secret, refresh_token):
        self.sender = sender
        self.recipient = recipient
        self.credentials = {
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }
        self.token = None

    @classmethod
    def from_environment(cls):
        return cls(*(required(name) for name in (
            "GMAIL_ADDRESS", "RECIPIENT_EMAIL", "GMAIL_CLIENT_ID",
            "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN",
        )))

    def authenticate(self):
        try:
            response = requests.post(
                "https://oauth2.googleapis.com/token", data=self.credentials,
                timeout=(10, 30), allow_redirects=False,
            )
            if response.status_code != 200:
                raise DeliveryError("Gmail OAuth configuration rejected")
            self.token = response.json()["access_token"]
            if not isinstance(self.token, str) or not self.token:
                raise ValueError("Empty token")
        except (requests.RequestException, ValueError, KeyError):
            raise DeliveryError("Gmail OAuth authentication failed") from None
        print("Gmail OAuth 인증 성공", flush=True)

    def send(self, message):
        if not self.token:
            raise DeliveryError("Authenticate before sending")
        try:
            response = requests.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")},
                timeout=(10, 60), allow_redirects=False,
            )
        except requests.RequestException:
            raise UncertainDelivery("Gmail response lost; inspect Sent mail") from None
        if response.status_code in (400, 401, 403, 404, 413, 429):
            raise RejectedDelivery(f"Gmail rejected send: HTTP {response.status_code}")
        if response.status_code != 200:
            raise UncertainDelivery("Unexpected Gmail response; inspect Sent mail")
        try:
            message_id = response.json()["id"]
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("Empty message id")
            return message_id
        except (ValueError, KeyError):
            raise UncertainDelivery("Gmail acknowledgement incomplete") from None


class DeliveryStore:
    """Metadata only: drafts, addresses and OAuth credentials never enter Git."""
    state_path = Path("delivery_state.json")
    used_path = Path("used_articles.txt")

    def __init__(self):
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = {"version": 1, "deliveries": {}}
        self.validate()

    def validate(self):
        if self.state.get("version") != 1 or not isinstance(self.state.get("deliveries"), dict):
            raise DeliveryError("Invalid delivery ledger")
        for key, record in self.state["deliveries"].items():
            if (record.get("status") not in ("prepared", "sending", "sent")
                    or record.get("date") != key
                    or not record.get("message_id")
                    or not record.get("article", {}).get("url")
                    or not record["article"].get("title")):
                raise DeliveryError("Invalid delivery record")

    def used(self):
        if not self.used_path.exists():
            return set()
        return set(self.used_path.read_text(encoding="utf-8").splitlines())

    @staticmethod
    def git(*args):
        # Never print remote errors: they can contain credential material.
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
        if result.returncode:
            raise DeliveryError("Git checkpoint failed; no automatic resend")
        return result.stdout.strip()

    def checkpoint(self):
        """A push must succeed BEFORE the caller may invoke Gmail send."""
        self.validate()
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise DeliveryError("Live delivery must run in the serialized GitHub workflow")
        branch = required("DELIVERY_BRANCH")
        if self.git("branch", "--show-current") != branch:
            raise DeliveryError("Unexpected checkout branch")
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        used = self.used()
        used.update(
            row["article"]["url"] for row in self.state["deliveries"].values()
            if row["status"] == "sent"
        )
        self.used_path.write_text("\n".join(sorted(used)) + ("\n" if used else ""), encoding="utf-8")
        paths = [str(self.state_path), str(self.used_path)]
        self.git("add", "--", *paths)
        if self.git("diff", "--cached", "--name-only", "--", *paths):
            self.git("commit", "-m", "Checkpoint PT mail delivery [skip ci]", "--", *paths)
        # Non-fast-forward conflicts fail closed; never rebase or force-push.
        self.git("push", "origin", f"HEAD:refs/heads/{branch}")


def run_delivery(store, mail, fetch, write, build_email, today):
    rows = store.state["deliveries"]
    if any(row["status"] == "sending" for row in rows.values()):
        print("발송 결과 확인 필요: Gmail 보낸편지함과 delivery_state.json을 확인하세요.", flush=True)
        raise UncertainDelivery("Unresolved send blocks further delivery")
    sent_urls = {row["article"]["url"] for row in rows.values() if row["status"] == "sent"}
    if not sent_urls.issubset(store.used()):
        store.checkpoint()
    pending = [row for row in rows.values() if row["status"] == "prepared"]
    if len(pending) > 1:
        raise DeliveryError("Multiple pending deliveries require review")
    if any(row["status"] == "sent" and row.get("sent_date", row["date"]) == today
           for row in rows.values()):
        print("오늘 초안은 이미 Gmail에 접수되었습니다. 추가 발송하지 않습니다.", flush=True)
        return
    # Check OAuth before spending Claude credits or changing article history.
    mail.authenticate()
    if pending:
        record = pending[0]
    else:
        unused = [article for article in fetch() if article["url"] not in store.used()]
        if not unused:
            print("새로운 기사가 없습니다.", flush=True)
            return
        result = write(unused)
        article = next(a for a in unused if a["url"] == result["url"])
        record = {
            "date": today, "status": "prepared", "article": article,
            "message_id": f"<pt-{uuid.uuid4().hex}@pt-blog-automation.invalid>",
        }
        rows[today] = record
        store.checkpoint()
    if pending:
        # Retry the same article even if it has left RSS; keep draft text private.
        result = write([record["article"]])
    message = build_email(result, mail.sender, mail.recipient, record["message_id"])
    record["status"] = "sending"
    store.checkpoint()
    try:
        gmail_id = mail.send(message)
    except RejectedDelivery:
        record["status"] = "prepared"
        store.checkpoint()
        raise
    # Timeouts, termination or unknown errors leave remote state "sending".
    record["status"] = "sent"
    record["sent_date"] = today
    record["gmail_id"] = gmail_id
    store.checkpoint()
    print("Gmail 접수 및 사용 기록 저장 완료", flush=True)
