"""신규 크롤러 (childportal / otbrain / isorimall) 파싱 테스트."""
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crawler


def _mock_response(html):
    m = MagicMock()
    m.text = html
    m.raise_for_status = MagicMock()
    return m


CHILDPORTAL_HTML = """
<html><body>
<table class="tbl_head01">
  <tr><td>지역</td><td><a href="?bo_table=job&wr_id=1001">[서울/강남]작업치료사(감통) 선생님</a></td></tr>
  <tr><td>지역</td><td><a href="?bo_table=job&wr_id=1002">[부산] 언어재활사 구인</a></td></tr>
  <tr><td>지역</td><td><a href="?bo_table=job&wr_id=1003">[경기/안성] 작업치료사 모집</a></td></tr>
  <tr><td>지역</td><td><a href="?bo_table=job&wr_id=1004">미술 치료사 선생님</a></td></tr>
</table>
</body></html>
"""


def test_childportal_filters_keyword_and_detects_seoul():
    with patch("crawler.requests.get", return_value=_mock_response(CHILDPORTAL_HTML)):
        jobs, status = crawler.crawl_childportal()
    assert status == "ok"
    titles = [j["title"] for j in jobs]
    assert any("강남" in t for t in titles)
    assert not any("언어재활사 구인" in t and "작업치료" not in t for t in titles)
    seoul = [j for j in jobs if j["location"] == "서울"]
    assert len(seoul) >= 1
    for j in jobs:
        assert j["url"].startswith("https://www.childportal.co.kr")
        assert j["source"] == "아이톡톡"


OTBRAIN_HTML = """
<html><body>
<table class="bd_lst bd_tb">
  <tr><td class="no">1</td><td class="cate">서울</td><td class="title"><a href="/index.php?mid=job&document_srl=644151">작업치료사 모집합니다</a></td></tr>
  <tr><td class="no">2</td><td class="cate">부산</td><td class="title"><a href="/index.php?mid=job&document_srl=644149">작업치료사(감통) 구인</a></td></tr>
  <tr><td class="no">3</td><td class="cate">서울</td><td class="title"><a href="/index.php?mid=job&document_srl=644100">자유게시판 공지사항</a></td></tr>
</table>
</body></html>
"""


def test_otbrain_parses_cate_and_title():
    with patch("crawler.requests.get", return_value=_mock_response(OTBRAIN_HTML)):
        jobs, status = crawler.crawl_otbrain()
    assert status == "ok"
    assert len(jobs) == 2
    assert jobs[0]["location"] == "서울"
    assert jobs[1]["location"] == "부산"
    assert all(j["url"].startswith("http://otbrain.com") for j in jobs)


ISORIMALL_HTML = """
<html><body>
<table>
  <tr>
    <td>159290</td><td>구인</td><td>D-7</td><td>해오름한방병원</td>
    <td><a href="View.asp?uuid=335374">[서울 강남] 작업치료사(감통) 모집</a></td>
    <td>2026-04-14 ~ 2026-04-21</td><td>04-14</td>
  </tr>
  <tr>
    <td>159289</td><td>구인</td><td>D-12</td><td>광주센터</td>
    <td><a href="View.asp?uuid=335373">광주) 행정실장 구인</a></td>
    <td>2026-04-13 ~ 2026-04-26</td><td>04-13</td>
  </tr>
  <tr>
    <td>159288</td><td>구인</td><td>D-21</td><td>부산아동센터</td>
    <td><a href="View.asp?uuid=335372">부산 작업치료사 구인</a></td>
    <td>2026-04-13 ~ 2026-05-05</td><td>04-13</td>
  </tr>
  <tr>
    <td>159287</td><td>구직</td><td>D-26</td><td>개인</td>
    <td><a href="View.asp?uuid=335371">서울 작업치료사 구직합니다</a></td>
    <td>2026-04-13 ~ 2026-05-10</td><td>04-13</td>
  </tr>
</table>
</body></html>
"""


def test_isorimall_parses_rows_and_skips_구직():
    with patch("crawler.requests.get", return_value=_mock_response(ISORIMALL_HTML)):
        jobs, status = crawler.crawl_isorimall()
    assert status == "ok"
    titles = [j["title"] for j in jobs]
    assert any("강남" in t for t in titles)
    assert any("부산 작업치료사" in t for t in titles)
    assert not any("행정실장" in t for t in titles)
    assert not any("구직합니다" in t for t in titles)
    j = next(j for j in jobs if "강남" in j["title"])
    assert j["org"] == "해오름한방병원"
    assert j["location"] == "서울"
    assert j["deadline"] == "2026-04-21"
    assert j["url"].startswith("https://isorimall.com/job-community/View.asp")


def test_all_crawlers_registered():
    names = [n for n, _ in crawler.ALL_CRAWLERS]
    assert "아이톡톡" in names
    assert "오티브레인" in names
    assert "아이소리몰" in names
