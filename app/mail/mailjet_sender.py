from __future__ import annotations

import httpx

from app.config import MailjetConfig


class MailjetMailSender:
    """Send transactional mail through Mailjet Send API v3.1."""

    def __init__(
        self,
        config: MailjetConfig,
        *,
        api_key: str,
        secret_key: str,
        timeout: float = 30.0,
    ) -> None:
        self.config = config
        self.api_key = api_key
        self.secret_key = secret_key
        self.timeout = timeout

    def send(
        self,
        *,
        subject: str,
        text: str,
        html: str,
        delivery_key: str,
        to_address: str | None = None,
    ) -> None:
        if not self.api_key or not self.secret_key:
            raise RuntimeError("MAILJET_API_KEY and MAILJET_SECRET_KEY are required")
        if not to_address:
            raise ValueError("a recipient address is required for Mailjet delivery")

        message: dict[str, object] = {
            "From": {
                "Email": self.config.from_address,
                "Name": self.config.from_name,
            },
            "To": [{"Email": to_address}],
            "Subject": subject,
            "TextPart": text,
            "HTMLPart": html,
            "CustomID": delivery_key,
            "Headers": {"X-QQ-Daily-Delivery-ID": delivery_key},
        }
        if self.config.reply_to_address:
            message["ReplyTo"] = {"Email": self.config.reply_to_address}

        response = httpx.post(
            f"{self.config.api_base_url.rstrip('/')}/send",
            auth=(self.api_key, self.secret_key),
            json={"Messages": [message]},
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Mailjet returned HTTP {response.status_code} with an invalid JSON response"
            ) from exc
        if response.status_code >= 400:
            error = payload
            if isinstance(payload, dict):
                error = payload.get("ErrorMessage") or payload.get("Messages") or payload
            raise RuntimeError(f"Mailjet returned HTTP {response.status_code}: {error}")

        messages = payload.get("Messages") if isinstance(payload, dict) else []
        messages = messages or []
        if not messages or str(messages[0].get("Status", "")).lower() != "success":
            raise RuntimeError(f"Mailjet did not accept the message: {payload}")
