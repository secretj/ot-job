"""
구조화 로깅 초기 스텁.

- Phase 4 C2에서 호출 사이트만 먼저 도입(get_logger).
- C3에서 structlog JSON 포맷 확정(configure_logging).
- 이 파일이 먼저 존재해야 app.py/crawler.py 의 import가 깨지지 않는다.
"""
from __future__ import annotations

import logging
import sys


_configured = False


def configure_logging() -> None:
    """기본 logging 초기화. C3에서 structlog로 교체될 예정."""
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _configured = True


def get_logger(name: str):
    """구조화 호출 호환 래퍼. structlog 스타일 kwargs를 받아 logging으로 전달."""
    configure_logging()
    return _KVLogger(logging.getLogger(name))


class _KVLogger:
    def __init__(self, base: logging.Logger):
        self._base = base

    def _fmt(self, event: str, **kv) -> str:
        if not kv:
            return event
        parts = " ".join(f"{k}={v}" for k, v in kv.items())
        return f"{event} {parts}"

    def info(self, event: str, **kv):
        self._base.info(self._fmt(event, **kv))

    def warning(self, event: str, **kv):
        self._base.warning(self._fmt(event, **kv))

    def error(self, event: str, **kv):
        self._base.error(self._fmt(event, **kv))

    def exception(self, event: str, **kv):
        self._base.exception(self._fmt(event, **kv))

    def debug(self, event: str, **kv):
        self._base.debug(self._fmt(event, **kv))
