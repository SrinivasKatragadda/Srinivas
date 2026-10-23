"""OAuth 2.0 support in ServiceNowClient (no network: requests calls are replaced)."""

import pytest
import requests

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.errors import ServiceNowError


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.ok = 200 <= status < 300
        self.content = b"x"
        self.reason = "x"
        self.headers = {}

    def json(self):
        return self._body


@pytest.fixture
def oauth_env(monkeypatch):
    monkeypatch.setenv("SERVICENOW_CLIENT_ID", "cid")
    monkeypatch.setenv("SERVICENOW_CLIENT_SECRET", "csecret")
    monkeypatch.delenv("SERVICENOW_OAUTH_GRANT", raising=False)


def _client(monkeypatch, api_statuses, token_bodies):
    token_calls, api_calls = [], []

    def fake_post(url, data=None, **kw):
        token_calls.append(dict(data))
        return Resp(200, token_bodies.pop(0)) if token_bodies else Resp(401, {"error": "invalid_grant"})

    def fake_request(self, method, url, **kw):
        api_calls.append(kw)
        status = api_statuses.pop(0) if api_statuses else 200
        return Resp(status, {"result": [{"sys_id": "1"}]} if status == 200 else {"error": {"message": "nope"}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests.Session, "request", fake_request)
    c = ServiceNowClient(instance_url="https://x.service-now.com", username="u", password="p")
    return c, token_calls, api_calls


def test_password_grant_bearer_and_token_cache(monkeypatch, oauth_env):
    c, tokens, calls = _client(monkeypatch, [], [{"access_token": "T1", "refresh_token": "R1", "expires_in": 1800}])
    c.query_records("incident", limit=1)
    c.query_records("incident", limit=1)
    assert len(tokens) == 1 and tokens[0]["grant_type"] == "password" and tokens[0]["username"] == "u"
    assert all(k["headers"] == {"Authorization": "Bearer T1"} and k["auth"] is None for k in calls)


def test_401_refreshes_once_with_refresh_token(monkeypatch, oauth_env):
    c, tokens, calls = _client(monkeypatch, [401, 200],
                               [{"access_token": "T1", "refresh_token": "R1", "expires_in": 1800},
                                {"access_token": "T2", "expires_in": 1800}])
    assert c.query_records("incident", limit=1) == [{"sys_id": "1"}]
    assert tokens[1]["grant_type"] == "refresh_token" and tokens[1]["refresh_token"] == "R1"
    assert calls[-1]["headers"] == {"Authorization": "Bearer T2"}


def test_bad_client_credentials_raise_oauth_error(monkeypatch, oauth_env):
    c, _, _ = _client(monkeypatch, [], [])
    with pytest.raises(ServiceNowError) as e:
        c.query_records("incident", limit=1)
    assert e.value.code == "OAUTH_ERROR" and "Application Registry" in e.value.detail


def test_client_credentials_grant_needs_no_user(monkeypatch, oauth_env):
    monkeypatch.setenv("SERVICENOW_OAUTH_GRANT", "client_credentials")
    c, tokens, _ = _client(monkeypatch, [], [{"access_token": "T1", "expires_in": 1800}])
    c.username = c.password = ""
    c.query_records("incident", limit=1)
    assert tokens[0]["grant_type"] == "client_credentials" and "username" not in tokens[0]


def test_basic_auth_when_oauth_not_configured(monkeypatch):
    monkeypatch.delenv("SERVICENOW_CLIENT_ID", raising=False)
    monkeypatch.delenv("SNOW_CLIENT_ID", raising=False)
    monkeypatch.delenv("SERVICENOW_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SNOW_CLIENT_SECRET", raising=False)
    c, tokens, calls = _client(monkeypatch, [], [])
    c.query_records("incident", limit=1)
    assert not tokens and calls[0]["auth"] == ("u", "p") and calls[0]["headers"] is None
