"""
중복 공고 병합 테스트
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
import app as app_mod


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


def test_group_duplicates_merges_sources():
    jobs = [
        {"id": "1", "source": "사람인", "org": "A병원", "title": "작업치료사 채용",
         "url": "https://s.com/1", "crawled_at": "2026-04-10T10:00", "is_new": 1, "read": False},
        {"id": "2", "source": "잡코리아", "org": "A병원", "title": "[모집] 작업치료사",
         "url": "https://j.com/2", "crawled_at": "2026-04-11T10:00", "is_new": 0, "read": False},
    ]
    grouped = app_mod._group_duplicates(jobs)
    assert len(grouped) == 1
    g = grouped[0]
    assert g["dup_count"] == 2
    assert len(g["sources"]) == 2
    # is_new 멤버가 대표
    assert g["is_new"] == 1
    assert g["source"] == "사람인"


def test_group_duplicates_separate_when_org_differs():
    jobs = [
        {"id": "1", "source": "사람인", "org": "A병원", "title": "작업치료사", "url": "u1",
         "crawled_at": "t1", "is_new": 1, "read": False},
        {"id": "2", "source": "사람인", "org": "B병원", "title": "작업치료사", "url": "u2",
         "crawled_at": "t2", "is_new": 1, "read": False},
    ]
    grouped = app_mod._group_duplicates(jobs)
    assert len(grouped) == 2


def test_group_duplicates_read_all_members():
    jobs = [
        {"id": "1", "source": "사람인", "org": "A", "title": "OT채용", "url": "u1",
         "crawled_at": "t", "is_new": 0, "read": True},
        {"id": "2", "source": "잡코리아", "org": "A", "title": "OT채용", "url": "u2",
         "crawled_at": "t", "is_new": 0, "read": False},
    ]
    grouped = app_mod._group_duplicates(jobs)
    assert grouped[0]["read"] is False  # 한쪽만 읽음 → 그룹은 안읽음
