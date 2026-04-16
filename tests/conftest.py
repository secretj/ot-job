"""pytest 공통 픽스처.

Neon Postgres(.env의 DATABASE_URL)를 테스트 DB로 사용한다.
각 테스트 함수 시작 전에 4개 테이블을 TRUNCATE하여 격리.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# .env 로드 (python-dotenv 의존 회피, 수동 파싱)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

os.environ.setdefault("KAKAO_REST_API_KEY", "test")
os.environ.setdefault("KAKAO_REDIRECT_URI", "http://localhost/cb")
os.environ.setdefault("FLASK_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
os.environ["ENABLE_SCHEDULER"] = "0"

import pytest

from db import get_conn


@pytest.fixture(autouse=True)
def _clean_db():
    """테스트 시작 전 4개 테이블 초기화."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE TABLE job_reads, crawl_log, jobs, users RESTART IDENTITY CASCADE"
            )
    yield
