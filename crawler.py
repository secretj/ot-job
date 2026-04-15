#!/usr/bin/env python3
"""
OT 채용 트래커 - 크롤러
서울 지역 작업치료사 / 감각통합치료사 채용 공고 수집
"""

import os
import time
import json
import re
import random
import hashlib
from datetime import datetime, date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from db import get_conn
from logging_setup import get_logger

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"

log = get_logger("crawler")

# ── User-Agent 풀 ──
UA_LIST = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def headers():
    return {
        "User-Agent": random.choice(UA_LIST),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    }

_DEDUP_STRIP_RE = re.compile(r"[\s\[\]\(\)\-_/·•,.!~]+")
_DEDUP_NOISE = ("채용", "모집", "공고", "정규직", "경력", "신입", "수시")


def dedup_key(org, title):
    """org+title 정규화해 출처가 다른 중복 공고를 같은 키로."""
    o = (org or "").strip().lower()
    t = (title or "").lower()
    for n in _DEDUP_NOISE:
        t = t.replace(n, "")
    t = _DEDUP_STRIP_RE.sub("", t)
    o = _DEDUP_STRIP_RE.sub("", o)
    return f"{o}::{t}"


def make_id(title, source):
    raw = f"{source}:{title}".encode()
    return hashlib.md5(raw).hexdigest()[:16]

def polite_sleep():
    time.sleep(random.uniform(2.0, 4.5))


# ══════════════════════════════════════════
#  DB (MariaDB)
#
#  스키마 DDL은 schema.sql + app.init_db()에서 관리.
#  여기서는 INSERT 전용 헬퍼만 유지.
# ══════════════════════════════════════════
def init_db():
    """하위 호환용 no-op. 실제 스키마 로드는 app.init_db() 담당."""
    return None


def insert_job(conn, job):
    """새 공고면 True, 기존이면 False.

    conn: pymysql Connection (컨텍스트 매니저로 get_conn()에서 얻은 것).
    """
    url = job.get("url", "")
    if not url or not url.startswith(("http://", "https://")):
        return False
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM jobs WHERE id = %s", (job["id"],))
        if cur.fetchone():
            return False
        cur.execute(
            "INSERT INTO jobs (id, source, title, org, location, job_type, deadline, url, crawled_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                job["id"], job["source"], job["title"], job["org"], job["location"],
                job["job_type"], job["deadline"], job["url"], datetime.now().isoformat(),
            ),
        )
    return True


def log_crawl(conn, source, found, new_count, status="ok"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crawl_log (timestamp, source, found, new_count, status) "
            "VALUES (%s,%s,%s,%s,%s)",
            (datetime.now().isoformat(), source, found, new_count, status),
        )


# ══════════════════════════════════════════
#  크롤러들
# ══════════════════════════════════════════

DEFAULT_KEYWORDS = ["작업치료", "감각통합", "OT ", "인지치료", "요양병원"]
DEFAULT_REGIONS = ["서울"]

# 런타임에 app.py가 유저 커스텀과 합쳐 주입. 비어있으면 DEFAULT 사용.
EXTRA_KEYWORDS: list = []
EXTRA_REGIONS: list = []

# 호환용 별칭
KEYWORDS = DEFAULT_KEYWORDS
SEOUL_FILTER = DEFAULT_REGIONS

# 정규직 판별 정책
FULLTIME_TOKENS = ["정규직"]
NON_FULLTIME_TOKENS = ["계약직", "파트타임", "파트", "아르바이트", "알바", "인턴", "프리랜서", "일용직"]


def effective_keywords():
    return DEFAULT_KEYWORDS + EXTRA_KEYWORDS


def effective_regions():
    return DEFAULT_REGIONS + EXTRA_REGIONS


def is_seoul(text):
    return any(k in text for k in effective_regions())


def matches_region(text, regions=None):
    src = regions if regions is not None else effective_regions()
    return any(k in text for k in src)


def matches_keyword(text, keywords=None):
    src = keywords if keywords is not None else effective_keywords()
    return any(k in text for k in src)


OPEN_ENDED_MARKERS = ["상시", "수시", "채용시", "채용 시", "충원시", "충원 시", "연중"]


def parse_deadline(text, today=None):
    """
    마감일 문자열 파싱.
    반환:
      - date: 파싱된 마감일 (해당일까지 유효)
      - None: 파싱 불가 또는 상시/수시 (만료 취급 X)
    """
    if not text:
        return None
    t = text.strip()
    if any(m in t for m in OPEN_ENDED_MARKERS):
        return None

    today = today or date.today()

    # "D-5", "D-0"
    m = re.search(r"[dD]\s*-\s*(\d+)", t)
    if m:
        return today + timedelta(days=int(m.group(1)))
    if "오늘마감" in t:
        return today
    if "내일마감" in t:
        return today + timedelta(days=1)

    # "2026-11-15", "2026.11.15", "2026/11/15"
    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    # "11/15", "~11/15", "11.15"
    m = re.search(r"(\d{1,2})[./](\d{1,2})", t)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            y = today.year
            try:
                candidate = date(y, month, day)
            except ValueError:
                return None
            # 오늘보다 6개월 이상 과거면 다음 해로
            if (today - candidate).days > 180:
                try:
                    candidate = date(y + 1, month, day)
                except ValueError:
                    return None
            return candidate

    return None


def is_expired(deadline_text, today=None):
    """파싱 가능하고 오늘보다 과거면 True. 나머지는 False."""
    d = parse_deadline(deadline_text, today=today)
    if d is None:
        return False
    return d < (today or date.today())


def normalize_url(href, base):
    """href 정제. 유효한 절대 URL이면 반환, 아니면 None."""
    if not href:
        return None
    h = href.strip()
    if h.lower().startswith(("javascript:", "mailto:", "#")):
        return None
    if h.startswith("http://") or h.startswith("https://"):
        return h
    if h.startswith("/"):
        return base.rstrip("/") + h
    return None


def classify_job_type(raw, full_text=""):
    """
    job_type 분류:
      - "정규직" (확실한 정규직)
      - "미확인" (불명)
      - None (계약직 등 명확히 비정규직 → 버림)
    """
    combined = f"{raw} {full_text}"
    if any(t in combined for t in NON_FULLTIME_TOKENS):
        return None
    if any(t in combined for t in FULLTIME_TOKENS):
        return "정규직"
    return "미확인"


MAX_PAGES = int(os.environ.get("CRAWL_MAX_PAGES", "5"))


# ── 사람인 ──
def crawl_saramin():
    source = "사람인"
    jobs = []
    seen = set()
    base = "https://www.saramin.co.kr/zf_user/search?searchType=search&searchword=%EC%84%9C%EC%9A%B8+%EC%9E%91%EC%97%85%EC%B9%98%EB%A3%8C%EC%82%AC&panel_type=&search_optional_item=y&search_done=y&panel_count=y&preview=y"

    try:
        for page in range(1, MAX_PAGES + 1):
            url = f"{base}&recruitPage={page}"
            r = requests.get(url, headers=headers(), timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            items = soup.select(".item_recruit")
            if not items:
                break

            page_added = 0
            for item in items:
                title_el = item.select_one(".job_tit a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                link = "https://www.saramin.co.kr" + title_el.get("href", "")

                corp_el = item.select_one(".corp_name a")
                org = corp_el.get_text(strip=True) if corp_el else ""

                conditions = [el.get_text(strip=True) for el in item.select(".job_condition span")]
                location = conditions[0] if conditions else ""
                job_type = conditions[2] if len(conditions) > 2 else ""

                deadline_el = item.select_one(".job_date .date")
                deadline = deadline_el.get_text(strip=True) if deadline_el else ""
                if is_expired(deadline):
                    continue

                full_text = f"{title} {org} {location}"
                if not (is_seoul(full_text) and matches_keyword(full_text)):
                    continue
                jt = classify_job_type(job_type, full_text)
                if jt is None:
                    continue

                job_id = make_id(title + org, "saramin")
                if job_id in seen:
                    continue
                seen.add(job_id)

                jobs.append({
                    "id": job_id,
                    "source": source,
                    "title": title,
                    "org": org,
                    "location": location,
                    "job_type": jt,
                    "deadline": deadline,
                    "url": link,
                })
                page_added += 1

            if page_added == 0:
                break
            polite_sleep()
    except Exception as e:
        log.error(f"[사람인] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ── 잡코리아 ──
def crawl_jobkorea():
    """
    2026년 리뉴얼된 잡코리아 검색 결과는 React 기반 마크업으로
    `.list-default .list-post` 클래스가 사라지고 `div.shadow-list`
    카드 안에 `/Recruit/GI_Read/` 링크와 회사/지역 span이 배치됨.
    """
    source = "잡코리아"
    jobs = []
    seen = set()
    base = "https://www.jobkorea.co.kr/Search/?stext=%EC%84%9C%EC%9A%B8+%EC%9E%91%EC%97%85%EC%B9%98%EB%A3%8C%EC%82%AC"
    try:
        for page in range(1, MAX_PAGES + 1):
            url = f"{base}&tabType=recruit&Page_No={page}"
            r = requests.get(url, headers=headers(), timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            cards = soup.select("div.shadow-list")
            if not cards:
                # 구버전 레이아웃 fallback
                cards = soup.select(".list-default .list-post")
            if not cards:
                break

            page_added = 0
            for card in cards:
                anchors = [
                    a for a in card.select("a[href*='/Recruit/GI_Read/']")
                    if a.get_text(strip=True)
                ]
                if not anchors:
                    continue
                # 가장 긴 텍스트를 가진 anchor = 공고 제목, 두 번째는 대체로 회사명
                anchors.sort(key=lambda a: len(a.get_text(strip=True)), reverse=True)
                title_el = anchors[0]
                title = title_el.get_text(strip=True)
                link = title_el.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://www.jobkorea.co.kr" + link

                # 회사명: title anchor 외에 짧은 텍스트의 link anchor
                org = ""
                for a in anchors[1:]:
                    t = a.get_text(strip=True)
                    if t and t != title:
                        org = t
                        break

                # 지역: 카드 span 중 '서울/경기/...' 시도 문자열 포함
                REGION_TOKENS = (
                    "서울", "경기", "인천", "부산", "대구", "대전", "광주",
                    "울산", "세종", "강원", "충북", "충남", "전북", "전남",
                    "경북", "경남", "제주",
                )
                location = ""
                # 하위에 또 span 이 있는 wrapper 는 건너뛰고 리프 span 중 지역 표기만 매칭
                for span in card.select("span"):
                    if span.find("span"):
                        continue
                    t = span.get_text(strip=True)
                    if not t or t == title or t == org:
                        continue
                    if any(t.startswith(r) for r in REGION_TOKENS) and len(t) < 40:
                        location = t
                        break

                full_text = f"{title} {org} {location}"
                if not (is_seoul(full_text) and matches_keyword(full_text)):
                    continue
                jt = classify_job_type("", full_text)
                if jt is None:
                    continue

                job_id = make_id(title + org, "jobkorea")
                if job_id in seen:
                    continue
                seen.add(job_id)

                jobs.append({
                    "id": job_id,
                    "source": source,
                    "title": title,
                    "org": org,
                    "location": location or "서울",
                    "job_type": jt,
                    "deadline": "",
                    "url": link,
                })
                page_added += 1

            if page_added == 0:
                break
            polite_sleep()
    except Exception as e:
        log.error(f"[잡코리아] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ── Indeed ──
def crawl_indeed():
    """
    Indeed KR은 2025년 말부터 데이터센터 IP + 기본 UA 요청에 대해 403을 내려
    requests 기반으로는 수집이 불가능. (JS 챌린지 / Cloudflare 유사 WAF)
    브라우저 자동화(playwright) 없이는 우회 시도 자체가 부담이라
    함수는 유지하되 즉시 차단 상태로 리턴한다.
    ALL_CRAWLERS에서 제외.
    """
    return [], "disabled: indeed blocks non-browser traffic with 403"


# ── 땡큐오티 ──
def crawl_thankyouot():
    """
    땡큐오티 구인 게시판은 imweb 기반 SPA 비슷한 렌더링이라
    전통적 `<tr>` 구조가 거의 없다. 실제 공고 링크는 `/board1/<번호>`
    패턴으로 노출되므로 해당 anchor만 스크래핑.
    여러 페이지를 순회해 가능한 많은 공고를 확보한다.
    """
    source = "땡큐오티"
    jobs = []
    seen = set()
    base_list = "https://thankyouot.com/board1"
    try:
        for page in range(1, MAX_PAGES + 1):
            url = base_list if page == 1 else f"{base_list}?page={page}"
            r = requests.get(url, headers=headers(), timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            page_added = 0
            for a in soup.select('a[href*="/board1/"]'):
                href = a.get("href", "")
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 5:
                    continue
                # "/board1/2872" 같이 숫자 뒤에 바로 끝나는 상세 링크만
                m = re.search(r"/board1/(\d+)(?:[/?#]|$)", href)
                if not m:
                    continue
                if not href.startswith("http"):
                    href = "https://thankyouot.com" + href if href.startswith("/") else "https://thankyouot.com/" + href

                if not matches_keyword(title):
                    continue

                location = "서울" if is_seoul(title) else "전국/미상"
                jt = classify_job_type("", title)
                if jt is None:
                    continue

                job_id = make_id(title + m.group(1), "thankyouot")
                if job_id in seen:
                    continue
                seen.add(job_id)

                jobs.append({
                    "id": job_id,
                    "source": source,
                    "title": title,
                    "org": "",
                    "location": location,
                    "job_type": jt,
                    "deadline": "",
                    "url": href,
                })
                page_added += 1

            if page_added == 0:
                break
            polite_sleep()
    except Exception as e:
        log.error(f"[땡큐오티] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ── 정신건강작업치료사회 ──
def crawl_kaotmh():
    source = "정신건강OT"
    jobs = []
    url = "http://www.kaotmh.org/bbs/bbr_6"
    try:
        r = requests.get(url, headers=headers(), timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        for link_el in soup.select("a"):
            title = link_el.get_text(strip=True)
            href = link_el.get("href", "")

            if not any(k in title for k in ["채용", "구인", "모집", "공고"]):
                continue
            if not href or href.lower().startswith(("javascript:", "mailto:", "#")):
                continue
            if not href.startswith("http"):
                if href.startswith("/"):
                    href = "http://www.kaotmh.org" + href
                else:
                    continue

            location = "서울" if is_seoul(title) else "전국/미상"

            jt = classify_job_type("", title)
            if jt is None:
                continue
            jobs.append({
                "id": make_id(title, "kaotmh"),
                "source": source,
                "title": title,
                "org": "",
                "location": location,
                "job_type": jt,
                "deadline": "",
                "url": href,
            })
    except Exception as e:
        log.error(f"[정신건강OT] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ══════════════════════════════════════════
#  메인 크롤 실행
# ══════════════════════════════════════════

# ── 아이톡톡 홈티 (childportal) ──
def crawl_childportal():
    """
    gnuboard 기반 게시판. 글 목록은 `?bo_table=job&page=N` 으로 페이지네이션.
    제목에는 `지역|기관명공고제목` 형태로 prefix가 붙는다.
    """
    source = "아이톡톡"
    jobs = []
    seen = set()
    base = "https://www.childportal.co.kr/board/bbs/board.php?bo_table=job"
    try:
        for page in range(1, MAX_PAGES + 1):
            url = base if page == 1 else f"{base}&page={page}"
            r = requests.get(url, headers=headers(), timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            anchors = soup.select('a[href*="wr_id="]')
            if not anchors:
                break

            page_added = 0
            for link_el in anchors:
                title = link_el.get_text(strip=True)
                href = link_el.get("href", "")
                if not title or len(title) < 5:
                    continue
                if not matches_keyword(title):
                    continue
                if not href.startswith("http"):
                    if href.startswith("/"):
                        href = "https://www.childportal.co.kr" + href
                    else:
                        href = "https://www.childportal.co.kr/board/bbs/" + href.lstrip("./")

                jt = classify_job_type("", title)
                if jt is None:
                    continue
                # 제목 "서울|기관명..." 형태면 지역 추출
                location = "전국/미상"
                m = re.match(r"^(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)\|", title)
                if m:
                    location = m.group(1)
                elif is_seoul(title):
                    location = "서울"

                # 서울 기준 필터 (다른 지역은 수집하지 않음 — effective_regions 기준)
                if not matches_region(location + " " + title):
                    continue

                job_id = make_id(title, "childportal")
                if job_id in seen:
                    continue
                seen.add(job_id)

                jobs.append({
                    "id": job_id,
                    "source": source,
                    "title": title,
                    "org": "",
                    "location": location,
                    "job_type": jt,
                    "deadline": "",
                    "url": href,
                })
                page_added += 1

            if page_added == 0:
                break
            polite_sleep()
    except Exception as e:
        log.error(f"[아이톡톡] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ── 오티브레인 (otbrain) ──
def crawl_otbrain():
    source = "오티브레인"
    jobs = []
    url = "http://otbrain.com/index.php?mid=job"
    try:
        r = requests.get(url, headers=headers(), timeout=15, verify=False)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        for row in soup.select("table.bd_lst tr"):
            title_td = row.select_one("td.title a")
            if not title_td:
                continue
            title = title_td.get_text(strip=True)
            href = title_td.get("href", "")
            if not title or not matches_keyword(title):
                continue
            if not href.startswith("http"):
                if href.startswith("/"):
                    href = "http://otbrain.com" + href
                else:
                    continue

            cate_el = row.select_one("td.cate")
            cate = cate_el.get_text(strip=True) if cate_el else ""
            full_text = f"{cate} {title}"

            jt = classify_job_type("", full_text)
            if jt is None:
                continue
            location = "서울" if is_seoul(full_text) else (cate or "전국/미상")
            jobs.append({
                "id": make_id(title, "otbrain"),
                "source": source,
                "title": title,
                "org": "",
                "location": location,
                "job_type": jt,
                "deadline": "",
                "url": href,
            })
    except Exception as e:
        log.error(f"[오티브레인] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ── 아이소리몰 (isorimall) ──
def crawl_isorimall():
    source = "아이소리몰"
    jobs = []
    url = "https://isorimall.com/job-community/list.asp"
    try:
        r = requests.get(url, headers=headers(), timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        for row in soup.select("tr"):
            link_el = row.select_one('a[href*="View.asp"]')
            if not link_el:
                continue
            tds = row.select("td")
            if len(tds) < 5:
                continue
            qtype = tds[1].get_text(strip=True)
            if qtype and qtype != "구인":
                continue
            org = tds[3].get_text(strip=True)
            title = link_el.get_text(strip=True).rstrip("N").strip()
            deadline_text = tds[5].get_text(strip=True) if len(tds) > 5 else ""
            full_text = f"{org} {title}"

            if not matches_keyword(full_text):
                continue
            href = link_el.get("href", "")
            if not href.startswith("http"):
                href = "https://isorimall.com/job-community/" + href.lstrip("./")

            jt = classify_job_type("", full_text)
            if jt is None:
                continue
            location = "서울" if is_seoul(full_text) else "전국/미상"

            m = re.search(r"~\s*(20\d{2}[-./]\d{1,2}[-./]\d{1,2})", deadline_text)
            deadline = m.group(1) if m else ""

            jobs.append({
                "id": make_id(title + org, "isorimall"),
                "source": source,
                "title": title,
                "org": org,
                "location": location,
                "job_type": jt,
                "deadline": deadline,
                "url": href,
            })
    except Exception as e:
        log.error(f"[아이소리몰] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


ALL_CRAWLERS = [
    ("사람인", crawl_saramin),
    ("잡코리아", crawl_jobkorea),
    # ("Indeed", crawl_indeed),  # 403 차단 지속 — 비활성화 (2026-04 확인)
    ("땡큐오티", crawl_thankyouot),
    ("정신건강OT", crawl_kaotmh),
    ("아이톡톡", crawl_childportal),
    ("오티브레인", crawl_otbrain),
    ("아이소리몰", crawl_isorimall),
]

def run_crawl():
    """전체 크롤링 1회 실행. 새 공고 리스트 반환.

    전체 수집 동안 단일 커넥션을 공유해 루프마다 신규 커넥션 비용을 회피.
    """
    total_new = []
    with get_conn() as conn:
        for name, func in ALL_CRAWLERS:
            log.info("crawl.source.started", source=name)
            jobs, status = func()
            new_count = 0
            for job in jobs:
                if insert_job(conn, job):
                    new_count += 1
                    total_new.append(job)
            log_crawl(conn, name, len(jobs), new_count, status)
            log.info(
                "crawl.source.finished",
                source=name, found=len(jobs), new_count=new_count, status=status,
            )
            polite_sleep()

    log.info("crawl.run.done", total_new=len(total_new))
    return total_new


def run_once():
    """1회 크롤링 + 카카오 알림"""
    new_jobs = run_crawl()
    if new_jobs:
        try:
            from kakao_notify import send_new_jobs_kakao
            send_new_jobs_kakao(new_jobs)
        except Exception as e:
            log.warning(f"카카오 알림 실패: {e}")
    return new_jobs


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:
        # 1회만 실행
        run_once()
    else:
        # 스케줄러로 반복 실행
        from apscheduler.schedulers.blocking import BlockingScheduler

        config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
        interval = config.get("crawl_interval_minutes", 30)

        log.info(f"🚀 크롤러 시작! {interval}분 간격으로 실행")
        run_once()  # 첫 실행

        scheduler = BlockingScheduler()
        scheduler.add_job(run_once, "interval", minutes=interval)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("크롤러 종료")
