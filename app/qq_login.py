from __future__ import annotations

import hashlib
from threading import Lock
from urllib.parse import urlsplit

import httpx


class QQLoginUnavailable(RuntimeError):
    """Raised when the local QQ login service cannot be reached or authenticated."""


class NapCatWebUIGateway:
    """Keep the NapCat credential server-side while exposing QR login state."""

    def __init__(self, webui_url: str, token: str, *, timeout: float = 10.0) -> None:
        parsed = urlsplit(webui_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("napcat webui URL must be an HTTP(S) URL")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self._token = token.strip()
        self._timeout = timeout
        self._credential = ""
        self._lock = Lock()

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _authenticate(self) -> str:
        if not self._token:
            raise QQLoginUnavailable("QQ login is not configured on this server")
        token_hash = hashlib.sha256(f"{self._token}.napcat".encode()).hexdigest()
        try:
            response = httpx.post(
                f"{self.base_url}/api/auth/login", json={"hash": token_hash}, timeout=self._timeout
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise QQLoginUnavailable("The QQ login service is unavailable") from exc
        if payload.get("code") != 0:
            raise QQLoginUnavailable("The QQ login service rejected its server credential")
        credential = str((payload.get("data") or {}).get("Credential") or "")
        if not credential:
            raise QQLoginUnavailable("The QQ login service returned no credential")
        self._credential = credential
        return credential

    def _post(self, path: str) -> dict[str, object]:
        with self._lock:
            credential = self._credential or self._authenticate()
            for attempt in range(2):
                try:
                    response = httpx.post(
                        f"{self.base_url}/api{path}", json={},
                        headers={"Authorization": f"Bearer {credential}"}, timeout=self._timeout,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise QQLoginUnavailable("The QQ login service is unavailable") from exc
                if payload.get("code") == 0:
                    return payload.get("data") or {}
                if attempt == 0 and str(payload.get("message", "")).lower() == "unauthorized":
                    credential = self._authenticate()
                    continue
                raise QQLoginUnavailable(str(payload.get("message") or "QQ login request failed"))
        raise QQLoginUnavailable("QQ login request failed")

    def snapshot(self) -> dict[str, object]:
        data = self._post("/QQLogin/CheckLoginStatus")
        return {
            "available": True,
            "logged_in": bool(data.get("isLogin")),
            "offline": bool(data.get("isOffline")),
            "qr_code": str(data.get("qrcodeurl") or ""),
            "error": str(data.get("loginError") or ""),
        }

    def refresh_qr_code(self) -> dict[str, object]:
        self._post("/QQLogin/RefreshQRcode")
        return self.snapshot()
