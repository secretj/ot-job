#!/usr/bin/env python3
"""
MariaDB 커넥션 레이어.

환경변수:
  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
  DB_POOL_SIZE (default 5)

사용 예:
    from db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE kakao_id=%s", (kid,))
            row = cur.fetchone()   # dict

주의:
  - 모든 쿼리는 parameterized (%s placeholder). 문자열 포맷팅으로 값 삽입 금지.
  - 컨텍스트 매니저 종료 시 커밋. 예외 시 롤백.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from queue import Empty, Queue

import pymysql
from pymysql.cursors import DictCursor


def _cfg() -> dict:
    return {
        "host": os.environ.get("DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "database": os.environ["DB_NAME"],
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }


class _Pool:
    """간단한 thread-safe 커넥션 풀.

    SQLAlchemy/DBUtils 의존성 회피. 풀 고갈 시 on-demand 신규 생성 후 반환(오버플로 허용),
    다만 max_overflow를 넘기면 대기하지 않고 즉시 새 커넥션(단명) 생성.
    """

    def __init__(self, size: int):
        self.size = size
        self._q: Queue = Queue(maxsize=size)
        self._lock = threading.Lock()
        self._created = 0

    def _new_conn(self):
        return pymysql.connect(**_cfg())

    def acquire(self):
        try:
            conn = self._q.get_nowait()
        except Empty:
            with self._lock:
                if self._created < self.size:
                    self._created += 1
                    return self._new_conn()
            # 풀 포화 → overflow 단명 커넥션
            return self._new_conn()
        # 생존 확인
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return self._new_conn()

    def release(self, conn, discard: bool = False):
        if discard:
            try:
                conn.close()
            except Exception:
                pass
            return
        try:
            self._q.put_nowait(conn)
        except Exception:
            # 풀 가득 차면 폐기
            try:
                conn.close()
            except Exception:
                pass


_pool: _Pool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> _Pool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                size = int(os.environ.get("DB_POOL_SIZE", "5"))
                _pool = _Pool(size)
    return _pool


@contextmanager
def get_conn():
    """커넥션 컨텍스트. 정상 종료 시 커밋, 예외 시 롤백 후 풀 반환."""
    pool = _get_pool()
    conn = pool.acquire()
    discard = False
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            discard = True
        raise
    finally:
        pool.release(conn, discard=discard)


def reset_pool() -> None:
    """테스트 지원: 풀 초기화."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            while not _pool._q.empty():
                try:
                    c = _pool._q.get_nowait()
                    c.close()
                except Exception:
                    pass
        _pool = None
