import json
import logging

from app.logging_config import JsonFormatter
from app.security.redact import REDACTED, redact, redact_text


def test_sensitive_keys_are_masked() -> None:
    data = {"client_secret": "abc", "nested": {"Authorization": "Bearer xyz", "ok": "fine"}}
    out = redact(data)
    assert out["client_secret"] == REDACTED
    assert out["nested"]["Authorization"] == REDACTED
    assert out["nested"]["ok"] == "fine"


def test_lists_are_walked() -> None:
    assert redact([{"api_key": "k"}, "plain"]) == [{"api_key": REDACTED}, "plain"]


def test_bearer_token_in_text() -> None:
    assert "abcdefgh12345" not in redact_text("header Bearer abcdefgh12345 sent")


def test_anthropic_key_in_text() -> None:
    assert "sk-ant-" not in redact_text("key=sk-ant-api03-ABCDEFGH12345678")


def test_assignment_style_secret() -> None:
    assert "hunter22" not in redact_text("password=hunter22 user=x")


def test_email_is_shortened() -> None:
    assert redact_text("mail jane.doe@example.com now") == "mail j***@example.com now"


def test_scalars_pass_through() -> None:
    assert redact(5) == 5
    assert redact(None) is None


def test_json_formatter_redacts_extras_and_message() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token=abc12345 seen", None, None)
    record.paypal_response = {"access_token": "zzz"}
    payload = json.loads(JsonFormatter().format(record))
    assert "abc12345" not in payload["message"]
    assert payload["paypal_response"]["access_token"] == REDACTED
