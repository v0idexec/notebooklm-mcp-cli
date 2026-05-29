"""Tests for Google login hint email-field autofill."""

from unittest.mock import patch

from notebooklm_tools.utils.cdp import (
    _google_login_ws_urls,
    _prefill_google_identifier,
)


def test_prefill_google_identifier_types_email_and_clicks_next():
    def fake_cdp(_ws_url, method, params=None):
        if method == "Runtime.evaluate":
            expression = params["expression"]
            if "getBoundingClientRect" in expression:
                return {"result": {"value": {"found": True, "x": 10, "y": 20}}}
            if "ready:" in expression:
                return {"result": {"value": {"ready": True, "active": True}}}
            if "filled:" in expression:
                return {"result": {"value": {"filled": True, "value": "first@gmail.com"}}}
            return {"result": {"value": {"ready": True}}}
        return {}

    with patch("notebooklm_tools.utils.cdp.execute_cdp_command", side_effect=fake_cdp) as cdp:
        assert _prefill_google_identifier("ws://test", "first@gmail.com") is True

    calls = cdp.call_args_list
    runtime_calls = [call for call in calls if call.args[1] == "Runtime.evaluate"]
    expression = "\n".join(call.args[2]["expression"] for call in runtime_calls)
    assert "first@gmail.com" in expression
    assert "#identifierId" in expression
    assert 'input[name="identifier"]' in expression
    assert "#identifierNext" in expression
    assert all(call.args[2]["returnByValue"] is True for call in runtime_calls)
    assert any(call.args[1] == "Input.insertText" for call in calls)
    assert any(call.args[1] == "Input.dispatchMouseEvent" for call in calls)


def test_google_login_ws_urls_prefers_accounts_pages():
    pages = [
        {"url": "https://notebooklm.google.com/", "webSocketDebuggerUrl": "ws://localhost:9222/page-1"},
        {"url": "https://accounts.google.com/v3/signin/identifier", "webSocketDebuggerUrl": "ws://localhost:9222/page-2"},
    ]
    with patch("notebooklm_tools.utils.cdp.get_pages_by_cdp_url", return_value=pages):
        assert _google_login_ws_urls("http://127.0.0.1:9222", "ws://localhost:9222/fallback") == [
            "ws://127.0.0.1:9222/page-2",
            "ws://127.0.0.1:9222/fallback",
        ]
