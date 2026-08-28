import hashlib

from app.qq_login import NapCatWebUIGateway


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_gateway_hashes_admin_token_and_returns_only_browser_safe_login_state(monkeypatch):
    requests = []

    def fake_post(url, **kwargs):
        requests.append((url, kwargs))
        if url.endswith("/api/auth/login"):
            return FakeResponse({"code": 0, "data": {"Credential": "private-session"}})
        return FakeResponse({
            "code": 0,
            "data": {"isLogin": False, "isOffline": False, "qrcodeurl": "https://example.com/qr", "loginError": ""},
        })

    monkeypatch.setattr("app.qq_login.httpx.post", fake_post)
    gateway = NapCatWebUIGateway("http://127.0.0.1:6099/webui", "admin-token")
    result = gateway.snapshot()

    expected_hash = hashlib.sha256(b"admin-token.napcat").hexdigest()
    assert requests[0][1]["json"] == {"hash": expected_hash}
    assert requests[1][1]["headers"] == {"Authorization": "Bearer private-session"}
    assert result == {
        "available": True, "logged_in": False, "offline": False,
        "qr_code": "https://example.com/qr", "error": "",
    }
    assert "admin-token" not in repr(result)
    assert "private-session" not in repr(result)
