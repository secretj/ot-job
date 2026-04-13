"""
공고 URL 도달성 테스트 (배포된 앱의 DB에 저장된 URL 검증)
네트워크 필요. 각 URL에 HEAD → GET fallback 으로 상태 코드 확인.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("APP_BASE_URL", "https://ot-job-tracker.fly.dev")
TIMEOUT = 10
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"


def fetch_jobs():
    try:
        r = requests.get(f"{BASE_URL}/api/jobs", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        pytest.skip(f"API 호출 실패: {e}")


def check_url(url):
    """HEAD 먼저, 405/403이면 GET으로 재시도. (status, final_url) 반환."""
    headers = {"User-Agent": UA}
    try:
        r = requests.head(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code in (405, 403, 400):
            r = requests.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True, stream=True)
            r.close()
        return r.status_code, r.url
    except requests.RequestException as e:
        return None, str(e)


def test_all_job_urls_valid_scheme():
    jobs = fetch_jobs()
    invalid = [j for j in jobs if not (j.get("url", "").startswith(("http://", "https://")))]
    assert not invalid, f"http/https가 아닌 URL 발견: {[j['url'] for j in invalid]}"


def test_all_job_urls_reachable():
    jobs = fetch_jobs()
    if not jobs:
        pytest.skip("저장된 공고 없음")

    failures = []
    for j in jobs:
        status, final = check_url(j["url"])
        if status is None or status >= 400:
            failures.append({
                "source": j["source"],
                "title": j["title"][:50],
                "url": j["url"],
                "status": status,
                "detail": final,
            })

    if failures:
        msg = "\n".join(
            f"  [{f['source']}] {f['title']} → {f['status']} ({f['url']})"
            for f in failures
        )
        pytest.fail(f"도달 불가 URL {len(failures)}/{len(jobs)}:\n{msg}")


def test_job_urls_no_duplicates():
    jobs = fetch_jobs()
    urls = [j["url"] for j in jobs]
    assert len(urls) == len(set(urls)), "중복 URL 존재"
