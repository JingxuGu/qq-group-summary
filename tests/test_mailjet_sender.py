from __future__ import annotations

import pytest

from app.config import MailjetConfig
from app.mail.mailjet_sender import MailjetMailSender


class FakeResponse:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_mailjet_sender_uses_send_api_v31(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeResponse(200, {"Messages": [{"Status": "success"}]})

    monkeypatch.setattr("app.mail.mailjet_sender.httpx.post", fake_post)
    sender = MailjetMailSender(
        MailjetConfig(from_address="sender@example.com", from_name="QQ Group Summary"),
        api_key="public-key",
        secret_key="private-key",
    )
    sender.send(
        subject="Subscription confirmed",
        text="confirmed",
        html="<p>confirmed</p>",
        delivery_key="subscription-123",
        to_address="reader@example.com",
    )

    assert captured["url"] == "https://api.mailjet.com/v3.1/send"
    assert captured["auth"] == ("public-key", "private-key")
    message = captured["json"]["Messages"][0]
    assert message["From"]["Email"] == "sender@example.com"
    assert message["To"] == [{"Email": "reader@example.com"}]
    assert message["CustomID"] == "subscription-123"


def test_mailjet_sender_rejects_missing_credentials():
    sender = MailjetMailSender(
        MailjetConfig(from_address="sender@example.com"), api_key="", secret_key=""
    )
    with pytest.raises(RuntimeError, match="MAILJET_API_KEY"):
        sender.send(
            subject="test", text="test", html="<p>test</p>",
            delivery_key="test-1", to_address="reader@example.com",
        )


def test_mailjet_sender_surfaces_api_errors(monkeypatch):
    monkeypatch.setattr(
        "app.mail.mailjet_sender.httpx.post",
        lambda *args, **kwargs: FakeResponse(401, {"ErrorMessage": "Unauthorized"}),
    )
    sender = MailjetMailSender(
        MailjetConfig(from_address="sender@example.com"),
        api_key="bad-key",
        secret_key="bad-secret",
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        sender.send(
            subject="test", text="test", html="<p>test</p>",
            delivery_key="test-1", to_address="reader@example.com",
        )
