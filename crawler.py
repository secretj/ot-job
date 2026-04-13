#!/usr/bin/env python3
"""
OT 채용 트래커 - 크롤러
서울 지역 작업치료사 / 감각통합치료사 채용 공고 수집
"""

import sqlite3
import time
import json
import random
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "jobs.db"
CONFIG_PATH = BASE_DIR / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crawler")

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

def make_id(title, source):
    raw = f"{source}:{title}".encode()
    return hashlib.md5(raw).hexdigest()[:16]

def polite_sleep():
    time.sleep(random.uniform(2.0, 4.5))


# ══════════════════════════════════════════
#  DB
# ══════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            org TEXT,
            location TEXT,
            job_type TEXT,
            deadline TEXT,
            url TEXT,
            crawled_at TEXT,
            is_new INTEGER DEFAULT 1,
            notified INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            source TEXT,
            found INTEGER,
            new_count INTEGER,
            status TEXT
        )
    """)
    conn.commit()
    return conn

def insert_job(conn, job):
    """새 공고면 True, 기존이면 False"""
    url = job.get("url", "")
    if not url or not url.startswith(("http://", "https://")):
        return False
    cur = conn.execute("SELECT id FROM jobs WHERE id = ?", (job["id"],))
    if cur.fetchone():
        return False
    conn.execute(
        "INSERT INTO jobs (id, source, title, org, location, job_type, deadline, url, crawled_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (job["id"], job["source"], job["title"], job["org"], job["location"],
         job["job_type"], job["deadline"], job["url"], datetime.now().isoformat()),
    )
    conn.commit()
    return True

def log_crawl(conn, source, found, new_count, status="ok"):
    conn.execute(
        "INSERT INTO crawl_log (timestamp, source, found, new_count, status) VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(), source, found, new_count, status),
    )
    conn.commit()


# ══════════════════════════════════════════
#  크롤러들
# ══════════════════════════════════════════

KEYWORDS = ["작업치료", "감각통합", "OT ", "인지치료", "요양병원"]
SEOUL_FILTER = ["서울"]

# 정규직 판별 정책
FULLTIME_TOKENS = ["정규직"]
NON_FULLTIME_TOKENS = ["계약직", "파트타임", "파트", "아르바이트", "알바", "인턴", "프리랜서", "일용직"]


def is_seoul(text):
    return any(k in text for k in SEOUL_FILTER)


def matches_keyword(text):
    return any(k in text for k in KEYWORDS)


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


# ── 사람인 ──
def crawl_saramin():
    source = "사람인"
    jobs = []
    url = "https://www.saramin.co.kr/zf_user/search?searchType=search&searchword=%EC%84%9C%EC%9A%B8+%EC%9E%91%EC%97%85%EC%B9%98%EB%A3%8C%EC%82%AC&panel_type=&search_optional_item=y&search_done=y&panel_count=y&preview=y"
    try:
        r = requests.get(url, headers=headers(), timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        for item in soup.select(".item_recruit"):
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

            full_text = f"{title} {org} {location}"
            if not (is_seoul(full_text) and matches_keyword(full_text)):
                continue
            jt = classify_job_type(job_type, full_text)
            if jt is None:
                continue

            jobs.append({
                "id": make_id(title + org, "saramin"),
                "source": source,
                "title": title,
                "org": org,
                "location": location,
                "job_type": jt,
                "deadline": deadline,
                "url": link,
            })
    except Exception as e:
        log.error(f"[사람인] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ── 잡코리아 ──
def crawl_jobkorea():
    source = "잡코리아"
    jobs = []
    url = "https://www.jobkorea.co.kr/Search/?stext=%EC%84%9C%EC%9A%B8+%EC%9E%91%EC%97%85%EC%B9%98%EB%A3%8C%EC%82%AC"
    try:
        r = requests.get(url, headers=headers(), timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        for item in soup.select(".list-default .list-post"):
            title_el = item.select_one(".post-list-info a.title")
            if not title_el:
                # 대체 셀렉터
                title_el = item.select_one(".title")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            if link and not link.startswith("http"):
                link = "https://www.jobkorea.co.kr" + link

            corp_el = item.select_one(".post-list-corp a") or item.select_one(".name")
            org = corp_el.get_text(strip=True) if corp_el else ""

            loc_el = item.select_one(".loc")
            location = loc_el.get_text(strip=True) if loc_el else ""

            full_text = f"{title} {org} {location}"
            if not (is_seoul(full_text) and matches_keyword(full_text)):
                continue
            jt = classify_job_type("", full_text)
            if jt is None:
                continue

            jobs.append({
                "id": make_id(title + org, "jobkorea"),
                "source": source,
                "title": title,
                "org": org,
                "location": location,
                "job_type": jt,
                "deadline": "",
                "url": link,
            })
    except Exception as e:
        log.error(f"[잡코리아] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ── Indeed ──
def crawl_indeed():
    source = "Indeed"
    jobs = []
    url = "https://kr.indeed.com/jobs?q=%EC%9E%91%EC%97%85%EC%B9%98%EB%A3%8C%EC%82%AC&l=%EC%84%9C%EC%9A%B8"
    try:
        r = requests.get(url, headers=headers(), timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        for card in soup.select(".job_seen_beacon, .resultContent"):
            title_el = card.select_one("h2 a, .jobTitle a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            if link and not link.startswith("http"):
                link = "https://kr.indeed.com" + link

            comp_el = card.select_one("[data-testid='company-name'], .companyName")
            org = comp_el.get_text(strip=True) if comp_el else ""

            loc_el = card.select_one("[data-testid='text-location'], .companyLocation")
            location = loc_el.get_text(strip=True) if loc_el else ""

            full_text = f"{title} {org} {location}"
            if not matches_keyword(full_text):
                continue
            jt = classify_job_type("", full_text)
            if jt is None:
                continue

            jobs.append({
                "id": make_id(title + org, "indeed"),
                "source": source,
                "title": title,
                "org": org,
                "location": location if location else "서울",
                "job_type": jt,
                "deadline": "",
                "url": link,
            })
    except Exception as e:
        log.error(f"[Indeed] 크롤링 실패: {e}")
        return jobs, str(e)

    return jobs, "ok"


# ── 땡큐오티 ──
def crawl_thankyouot():
    source = "땡큐오티"
    jobs = []
    url = "https://thankyouot.com/board1"
    try:
        r = requests.get(url, headers=headers(), timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        for row in soup.select("tr"):
            link_el = row.select_one("a")
            if not link_el:
                continue
            title = link_el.get_text(strip=True)
            href = link_el.get("href", "")
            if not href or href.lower().startswith(("javascript:", "mailto:", "#")):
                continue
            if not href.startswith("http"):
                if href.startswith("/"):
                    href = "https://thankyouot.com" + href
                else:
                    continue

            # 서울 관련이거나 키워드 매칭
            if not matches_keyword(title):
                continue
            # 땡큐오티는 지역 구분이 없으므로 제목에서 서울 판별 시도
            location = "서울" if is_seoul(title) else "전국/미상"

            jt = classify_job_type("", title)
            if jt is None:
                continue
            jobs.append({
                "id": make_id(title, "thankyouot"),
                "source": source,
                "title": title,
                "org": "",
                "location": location,
                "job_type": jt,
                "deadline": "",
                "url": href,
            })
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

ALL_CRAWLERS = [
    ("사람인", crawl_saramin),
    ("잡코리아", crawl_jobkorea),
    ("Indeed", crawl_indeed),
    ("땡큐오티", crawl_thankyouot),
    ("정신건강OT", crawl_kaotmh),
]

def run_crawl():
    """전체 크롤링 1회 실행. 새 공고 리스트 반환."""
    conn = init_db()
    total_new = []

    log.info("=" * 50)
    log.info("크롤링 시작")

    for name, func in ALL_CRAWLERS:
        log.info(f"📡 [{name}] 수집 중...")
        jobs, status = func()
        new_count = 0

        for job in jobs:
            if insert_job(conn, job):
                new_count += 1
                total_new.append(job)

        log_crawl(conn, name, len(jobs), new_count, status)
        log.info(f"  → 수집 {len(jobs)}건, 신규 {new_count}건 {'✅' if status == 'ok' else '❌ ' + status}")
        polite_sleep()

    log.info(f"크롤링 완료! 총 신규 {len(total_new)}건")
    log.info("=" * 50)

    conn.close()
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
