"""
크롤러 정책 테스트: 키워드 매칭 + 정규직 분류
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import crawler


# ── 키워드 매칭 ──
def test_matches_keyword_ot():
    assert crawler.matches_keyword("서울 작업치료사 채용")
    assert crawler.matches_keyword("감각통합 치료사 모집")
    assert crawler.matches_keyword("OT 경력직")
    assert crawler.matches_keyword("인지치료 전문가")


def test_matches_keyword_nursing_hospital():
    assert crawler.matches_keyword("요양병원 간호조무사 모집")
    assert crawler.matches_keyword("강동 요양병원 채용")


def test_matches_keyword_reject():
    assert not crawler.matches_keyword("마케터 경력 채용")
    assert not crawler.matches_keyword("물리치료사 모집")


# ── 정규직 분류 ──
def test_classify_fulltime_explicit():
    assert crawler.classify_job_type("정규직", "") == "정규직"
    assert crawler.classify_job_type("", "서울 작업치료사 정규직 채용") == "정규직"


def test_classify_unknown():
    assert crawler.classify_job_type("", "") == "미확인"
    assert crawler.classify_job_type("", "요양병원 간호사 모집") == "미확인"


def test_classify_reject_nonfulltime():
    assert crawler.classify_job_type("계약직", "") is None
    assert crawler.classify_job_type("", "파트타임 작업치료사") is None
    assert crawler.classify_job_type("아르바이트", "") is None
    assert crawler.classify_job_type("인턴", "") is None
    assert crawler.classify_job_type("프리랜서", "") is None


def test_classify_reject_beats_fulltime():
    # "정규직 전환형 계약직" 같은 경우 — 계약직이 있으면 버림
    assert crawler.classify_job_type("", "정규직 전환 계약직") is None


# ── 서울 필터 ──
def test_seoul_filter():
    assert crawler.is_seoul("서울 강남구")
    assert not crawler.is_seoul("경기도 성남")


# ── URL 정규화 ──
def test_normalize_url_valid():
    assert crawler.normalize_url("https://a.com/x", "http://b.com") == "https://a.com/x"
    assert crawler.normalize_url("/x", "https://b.com") == "https://b.com/x"
    assert crawler.normalize_url("/x", "https://b.com/") == "https://b.com/x"


# ── 마감일 파싱 ──
TODAY = date(2026, 4, 13)


def test_parse_deadline_open_ended():
    assert crawler.parse_deadline("상시채용", today=TODAY) is None
    assert crawler.parse_deadline("수시모집", today=TODAY) is None
    assert crawler.parse_deadline("채용시 마감", today=TODAY) is None
    assert crawler.parse_deadline("", today=TODAY) is None


def test_parse_deadline_dday():
    assert crawler.parse_deadline("D-5", today=TODAY) == date(2026, 4, 18)
    assert crawler.parse_deadline("D-0", today=TODAY) == TODAY
    assert crawler.parse_deadline("오늘마감", today=TODAY) == TODAY
    assert crawler.parse_deadline("내일마감", today=TODAY) == date(2026, 4, 14)


def test_parse_deadline_full_date():
    assert crawler.parse_deadline("2026-11-15", today=TODAY) == date(2026, 11, 15)
    assert crawler.parse_deadline("2026.11.15", today=TODAY) == date(2026, 11, 15)
    assert crawler.parse_deadline("~2026/11/15", today=TODAY) == date(2026, 11, 15)


def test_parse_deadline_short_date():
    assert crawler.parse_deadline("11/15", today=TODAY) == date(2026, 11, 15)
    assert crawler.parse_deadline("~11/15(수)", today=TODAY) == date(2026, 11, 15)
    # 과거 월/일은 다음 해로 이월 (6개월 이상 과거)
    assert crawler.parse_deadline("1/5", today=date(2026, 9, 1)) == date(2027, 1, 5)


def test_is_expired():
    assert crawler.is_expired("2026-03-01", today=TODAY)  # 과거
    assert not crawler.is_expired("2026-11-15", today=TODAY)  # 미래
    assert not crawler.is_expired("상시채용", today=TODAY)  # 파싱 불가
    assert not crawler.is_expired("", today=TODAY)  # 빈값
    assert not crawler.is_expired("D-0", today=TODAY)  # 오늘 = 미만료


def test_normalize_url_reject():
    assert crawler.normalize_url("javascript:readArticle('1')", "https://b.com") is None
    assert crawler.normalize_url("JavaScript:void(0)", "https://b.com") is None
    assert crawler.normalize_url("#", "https://b.com") is None
    assert crawler.normalize_url("mailto:a@b.com", "https://b.com") is None
    assert crawler.normalize_url("", "https://b.com") is None
    assert crawler.normalize_url("relative/path", "https://b.com") is None
