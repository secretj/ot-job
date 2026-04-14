"""
structlog 기반 구조화 JSON 로깅.

설계 원칙:
  - stdout 단일 스트림으로 방출 → Docker log driver → Promtail → Loki
  - JSON 라인 포맷 (Loki 파싱 쉬움)
  - event 이름 표준화: snake_case + dot-namespace
      예) crawl.started, crawl.finished, crawl.source.finished,
          auth.login, auth.failed, notify.failed,
          db.init_failed, health.db_failed, scheduler.started
  - 공통 context 바인딩 가능: user_id, source, duration_ms

사용:
    from logging_setup import configure_logging, get_logger
    configure_logging()
    log = get_logger("app")
    log.info("auth.login", user_id=123)
"""
from __future__ import annotations

import logging
import os
import sys

import structlog


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # stdlib logging 기본 핸들러 (APScheduler/werkzeug 등 구조화 안 된 로거 대응)
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(message)s",
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=False)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str):
    configure_logging()
    return structlog.get_logger(name)
