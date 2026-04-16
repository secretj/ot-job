"""
중복 공고 dedup_key 테스트.

과거 Python 레벨 `_group_duplicates`는 DB 레벨 DISTINCT ON + window 쿼리로 대체됨.
그룹핑 통합 동작은 `tests/test_read_tracking.py`의 /api/jobs e2e 테스트로 커버.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("KAKAO_REST_API_KEY", "test")
os.environ.setdefault("KAKAO_REDIRECT_URI", "http://localhost/cb")
os.environ.setdefault("ENABLE_SCHEDULER", "0")
os.environ.setdefault("DB_PATH", tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)

import crawler


def test_dedup_key_same_posting_different_sources():
    k1 = crawler.dedup_key("강동성심병원", "작업치료사 정규직 채용")
    k2 = crawler.dedup_key("강동성심병원", "[모집] 작업치료사 정규직")
    assert k1 == k2


def test_dedup_key_different_orgs():
    k1 = crawler.dedup_key("A병원", "작업치료사 채용")
    k2 = crawler.dedup_key("B병원", "작업치료사 채용")
    assert k1 != k2


def test_dedup_key_whitespace_case_insensitive():
    k1 = crawler.dedup_key("A병원", "OT 채용")
    k2 = crawler.dedup_key(" A병원 ", "ot  채용")
    assert k1 == k2
