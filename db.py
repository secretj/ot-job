#!/usr/bin/env python3
"""
PostgreSQL (Neon) 커넥션 레이어.

환경변수:
  DATABASE_URL  e.g. postgresql://user:pass@host/db?sslmode=require

사용 예:
    from db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE kakao_id=%s", (kid,))
            row = cur.fetchone()   # dict

주의:
  - 모든 쿼리는 parameterized (%s placeholder). 문자열 포맷팅으로 값 삽입 금지.
  - 컨텍스트 매니저 종료 시 커밋. 예외 시 롤백.
  - serverless(Vercel) 환경에선 함수 호출당 단명 커넥션. Neon 측 PgBouncer가 풀링 담당.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def _dsn() -> str:
    try:
        return os.environ["DATABASE_URL"]
    except KeyError as e:
        raise RuntimeError("DATABASE_URL 환경변수가 필요합니다") from e


@contextmanager
def get_conn():
    """커넥션 컨텍스트. 정상 종료 시 커밋, 예외 시 롤백."""
    conn = psycopg.connect(_dsn(), row_factory=dict_row, autocommit=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def reset_pool() -> None:
    """테스트 지원 호환용 stub. 풀 없으니 noop."""
    return None
