import logging

import httpx
from openai import APIConnectionError, AuthenticationError

from app.qwen_client import _log_api_failure


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def _fake_response() -> httpx.Response:
    return httpx.Response(401, request=_fake_request())


def test_transient_error_logs_as_warning(caplog):
    exc = APIConnectionError(request=_fake_request())
    with caplog.at_level(logging.WARNING, logger="app.qwen_client"):
        _log_api_failure("bid", "qwen3.6-flash", exc)
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    assert "after exhausting retries" in caplog.text


def test_permanent_error_logs_as_error(caplog):
    exc = AuthenticationError("invalid api key", response=_fake_response(), body=None)
    with caplog.at_level(logging.WARNING, logger="app.qwen_client"):
        _log_api_failure("bid", "qwen3.6-flash", exc)
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert "after exhausting retries" not in caplog.text
