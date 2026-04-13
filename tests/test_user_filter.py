"""
유저별 맞춤 키워드/지역 필터 테스트
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ.setdefault("KAKAO_REST_API_KEY", "test")
os.environ.setdefault("KAKAO_REDIRECT_URI", "http://localhost/cb")
os.environ.setdefault("ENABLE_SCHEDULER", "0")

import crawler
import app as app_mod


def test_parse_csv_list_basic():
    assert app_mod._parse_csv_list("") == []
    assert app_mod._parse_csv_list(None) == []
    assert app_mod._parse_csv_list("방문재활, 소아작업치료") == ["방문재활", "소아작업치료"]


def test_parse_csv_list_dedupe_and_newline():
    assert app_mod._parse_csv_list("a\nb, a,  b ") == ["a", "b"]


def test_effective_keywords_merges_extra():
    crawler.EXTRA_KEYWORDS = ["방문재활"]
    try:
        kws = crawler.effective_keywords()
        assert "방문재활" in kws
        assert "작업치료" in kws
    finally:
        crawler.EXTRA_KEYWORDS = []


def test_matches_keyword_with_override():
    assert crawler.matches_keyword("방문재활 치료사", keywords=["방문재활"])
    assert not crawler.matches_keyword("방문재활 치료사", keywords=["다른키워드"])


def test_job_matches_user_default():
    user = {"custom_keywords": "[]", "custom_regions": "[]"}
    job = {"title": "서울 작업치료사 채용", "org": "A병원", "location": "서울 강남"}
    assert app_mod.job_matches_user(job, user)


def test_job_matches_user_custom_keyword():
    user = {"custom_keywords": '["방문재활"]', "custom_regions": "[]"}
    job = {"title": "방문재활 치료사", "org": "B", "location": "서울"}
    assert app_mod.job_matches_user(job, user)


def test_job_matches_user_region_filter():
    user = {"custom_keywords": "[]", "custom_regions": "[]"}
    job = {"title": "작업치료사 채용", "org": "A", "location": "부산 해운대"}
    assert not app_mod.job_matches_user(job, user)


def test_job_matches_user_custom_region():
    user = {"custom_keywords": "[]", "custom_regions": '["경기"]'}
    job = {"title": "작업치료사 채용", "org": "A", "location": "경기 성남"}
    assert app_mod.job_matches_user(job, user)


def test_job_matches_user_unknown_location_passes():
    user = {"custom_keywords": "[]", "custom_regions": "[]"}
    job = {"title": "작업치료사 채용", "org": "A", "location": "전국/미상"}
    assert app_mod.job_matches_user(job, user)


def test_job_matches_user_no_keyword_match():
    user = {"custom_keywords": "[]", "custom_regions": "[]"}
    job = {"title": "마케터 채용", "org": "A", "location": "서울"}
    assert not app_mod.job_matches_user(job, user)
