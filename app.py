#!/usr/bin/env python3
"""
OT 채용 트래커 - 멀티유저 웹앱 (Flask + Kakao OAuth + 크롤러 스케줄러)
MariaDB 백엔드.
"""
import os
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, request, redirect, session, render_template, jsonify, url_for
from apscheduler.schedulers.background import BackgroundScheduler

import crawler
import kakao_notify
from db import get_conn
from logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger("app")

# ── 환경변수 ──
KAKAO_REST_API_KEY = os.environ["KAKAO_REST_API_KEY"]
KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")
KAKAO_REDIRECT_URI = os.environ["KAKAO_REDIRECT_URI"]
CRAWL_INTERVAL_MINUTES = int(os.environ.get("CRAWL_INTERVAL_MINUTES", "30"))

# FLASK_SECRET_KEY: 운영 환경에서는 필수(환경변수 미설정 시 fail fast).
# 서버리스(cold start)마다 랜덤 생성되면 session cookie 복호화 실패로 세션이 풀리는
# 버그 재발 방지. 로컬 개발(FLASK_ENV=development 또는 FLASK_DEBUG=1)에서만 fallback 허용.
_secret = os.environ.get("FLASK_SECRET_KEY")
if not _secret:
    _is_dev = (
        os.environ.get("FLASK_ENV") == "development"
        or os.environ.get("FLASK_DEBUG") == "1"
    )
    if _is_dev:
        log.warning("flask.secret_key.missing_dev_fallback")
        _secret = secrets.token_hex(32)
    else:
        raise RuntimeError(
            "FLASK_SECRET_KEY 환경변수가 설정되어야 합니다. "
            "Vercel 대시보드 > Project > Settings > Environment Variables 에서 "
            "FLASK_SECRET_KEY 를 추가하세요 (예: `python -c \"import secrets; "
            "print(secrets.token_hex(32))\"` 출력값)."
        )
FLASK_SECRET_KEY = _secret

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


# ══════════════════════════════════════════
#  DB 초기화 (스키마 로드)
# ══════════════════════════════════════════
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _split_sql_statements(sql_text: str):
    """세미콜론 기반 분리. schema.sql은 trigger 등 복합문 없음 가정."""
    for stmt in sql_text.split(";"):
        s = stmt.strip()
        if s:
            yield s


def init_db():
    """MariaDB 스키마 로드. idempotent (CREATE TABLE IF NOT EXISTS)."""
    sql_text = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql_text):
                cur.execute(stmt)

    # 런타임 마이그레이션: custom_keywords/regions 컬럼이 없으면 추가
    # (schema.sql이 NOT NULL이라 새로 만들면 불필요하지만, 기존 환경 호환용)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'users'
                """
            )
            cols = {r["column_name"] for r in cur.fetchall()}
            if "custom_keywords" not in cols:
                cur.execute(
                    "ALTER TABLE users ADD COLUMN custom_keywords TEXT NOT NULL DEFAULT '[]'"
                )
            if "custom_regions" not in cols:
                cur.execute(
                    "ALTER TABLE users ADD COLUMN custom_regions TEXT NOT NULL DEFAULT '[]'"
                )

    # 런타임 마이그레이션: jobs.dedup_key 컬럼이 없으면 추가 + 기존 행 backfill
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'jobs'
                """
            )
            jcols = {r["column_name"] for r in cur.fetchall()}
            if "dedup_key" not in jcols:
                cur.execute("ALTER TABLE jobs ADD COLUMN dedup_key VARCHAR(512)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dedup_key ON jobs (dedup_key)")
            # NULL인 행 backfill (신규 컬럼 추가 직후 또는 이전 insert가 dedup_key 없던 시기)
            cur.execute("SELECT id, org, title FROM jobs WHERE dedup_key IS NULL")
            rows = cur.fetchall()
            if rows:
                cur.executemany(
                    "UPDATE jobs SET dedup_key = %s WHERE id = %s",
                    [(crawler.dedup_key(r["org"], r["title"]), r["id"]) for r in rows],
                )


# ══════════════════════════════════════════
#  DB - users / job_reads 엑세스
# ══════════════════════════════════════════
def mark_job_read(kakao_id, job_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO job_reads (kakao_id, job_id, read_at) VALUES (%s, %s, %s) ON CONFLICT (kakao_id, job_id) DO NOTHING",
                (kakao_id, job_id, datetime.now().isoformat()),
            )


def get_read_ids(kakao_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT job_id FROM job_reads WHERE kakao_id=%s", (kakao_id,))
            rows = cur.fetchall()
    return {r["job_id"] for r in rows}


def _parse_csv_list(raw):
    if not raw:
        return []
    items = []
    for token in raw.replace("\n", ",").split(","):
        t = token.strip()
        if t and t not in items:
            items.append(t)
    return items


def get_user_customs():
    """모든 활성 유저의 custom_keywords/regions 합집합 반환."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT custom_keywords, custom_regions FROM users WHERE enabled = TRUE")
            rows = cur.fetchall()
    kws, regs = set(), set()
    for r in rows:
        try:
            kws.update(json.loads(r["custom_keywords"] or "[]"))
            regs.update(json.loads(r["custom_regions"] or "[]"))
        except Exception:
            pass
    return sorted(kws), sorted(regs)


def set_user_settings(kakao_id, keywords, regions):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET custom_keywords=%s, custom_regions=%s WHERE kakao_id=%s",
                (
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(regions, ensure_ascii=False),
                    kakao_id,
                ),
            )


def get_user(kakao_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE kakao_id=%s", (kakao_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def job_matches_user(job, user):
    try:
        user_kw = json.loads(user.get("custom_keywords") or "[]")
        user_rg = json.loads(user.get("custom_regions") or "[]")
    except Exception:
        user_kw, user_rg = [], []
    kws = crawler.DEFAULT_KEYWORDS + user_kw
    regs = crawler.DEFAULT_REGIONS + user_rg
    full_text = f"{job.get('title','')} {job.get('org','')} {job.get('location','')}"
    if not any(k in full_text for k in kws):
        return False
    loc = job.get("location", "")
    if loc and "전국" not in loc and "미상" not in loc:
        if not any(r in (loc + " " + full_text) for r in regs):
            return False
    return True


def upsert_user(kakao_id, nickname, access_token, refresh_token, expires_at):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (kakao_id, nickname, access_token, refresh_token, expires_at, created_at, custom_keywords, custom_regions)
                VALUES (%s, %s, %s, %s, %s, %s, '[]', '[]')
                ON CONFLICT (kakao_id) DO UPDATE SET
                    nickname=EXCLUDED.nickname,
                    access_token=EXCLUDED.access_token,
                    refresh_token=EXCLUDED.refresh_token,
                    expires_at=EXCLUDED.expires_at
                """,
                (kakao_id, nickname, access_token, refresh_token, expires_at, datetime.now().isoformat()),
            )


def get_enabled_users():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE enabled = TRUE")
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def set_enabled(kakao_id, enabled):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET enabled = %s WHERE kakao_id = %s",
                (bool(enabled), kakao_id),
            )


def update_tokens(kakao_id, access_token, refresh_token, expires_at):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET access_token=%s, refresh_token=%s, expires_at=%s WHERE kakao_id=%s",
                (access_token, refresh_token, expires_at, kakao_id),
            )


# ══════════════════════════════════════════
#  Kakao OAuth
# ══════════════════════════════════════════
def kakao_auth_url():
    return (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={KAKAO_REST_API_KEY}"
        f"&redirect_uri={KAKAO_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=talk_message,profile_nickname"
    )


def exchange_code(code):
    data = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_REST_API_KEY,
        "redirect_uri": KAKAO_REDIRECT_URI,
        "code": code,
    }
    if KAKAO_CLIENT_SECRET:
        data["client_secret"] = KAKAO_CLIENT_SECRET
    r = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=10)
    r.raise_for_status()
    return r.json()


def fetch_profile(access_token):
    r = requests.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def expires_at_from_seconds(sec):
    from datetime import timedelta
    return (datetime.now() + timedelta(seconds=int(sec) - 60)).isoformat()


# ══════════════════════════════════════════
#  Routes
# ══════════════════════════════════════════
def _group_duplicates(jobs):
    groups = {}
    order = []
    for j in jobs:
        key = crawler.dedup_key(j.get("org", ""), j.get("title", ""))
        if key not in groups:
            groups[key] = dict(j)
            groups[key]["sources"] = []
            groups[key]["dup_count"] = 0
            order.append(key)
        g = groups[key]
        g["sources"].append({"source": j.get("source"), "url": j.get("url"), "id": j.get("id")})
        g["dup_count"] += 1
        if (j.get("is_new") and not g.get("is_new")) or (
            j.get("is_new") == g.get("is_new") and (j.get("crawled_at") or "") > (g.get("crawled_at") or "")
        ):
            for f in ("id", "source", "url", "crawled_at", "is_new", "deadline", "job_type", "location"):
                g[f] = j.get(f)
        g["read"] = g.get("read", True) and j.get("read", False)
    return [groups[k] for k in order]


def _attach_read_flag(jobs, me):
    if not me:
        for j in jobs:
            j["read"] = False
        return jobs
    read_ids = get_read_ids(me["id"])
    for j in jobs:
        j["read"] = j["id"] in read_ids
    return jobs


@app.route("/")
def index():
    me = session.get("user")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM jobs ORDER BY is_new DESC, crawled_at DESC LIMIT 200"
            )
            jobs = [dict(j) for j in cur.fetchall()]
    _attach_read_flag(jobs, me)
    jobs = _group_duplicates(jobs)
    if me:
        jobs.sort(key=lambda j: (j["read"], not j.get("is_new")))
    return render_template("index.html", jobs=jobs, me=me)


@app.route("/login")
def login():
    return redirect(kakao_auth_url())


@app.route("/kakao/callback")
def kakao_callback():
    code = request.args.get("code")
    if not code:
        log.warning("auth.failed", reason="missing_code")
        return "인증 실패: code 없음", 400
    try:
        tok = exchange_code(code)
        profile = fetch_profile(tok["access_token"])
    except requests.HTTPError as e:
        log.warning("auth.failed", reason="kakao_http_error", status=e.response.status_code)
        return f"카카오 인증 실패: {e.response.text}", 400

    kakao_id = profile["id"]
    nickname = profile.get("properties", {}).get("nickname", "")
    upsert_user(
        kakao_id=kakao_id,
        nickname=nickname,
        access_token=tok["access_token"],
        refresh_token=tok.get("refresh_token"),
        expires_at=expires_at_from_seconds(tok.get("expires_in", 21600)),
    )
    session["user"] = {"id": kakao_id, "nickname": nickname}
    session.permanent = True
    log.info("auth.login", user_id=kakao_id)
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    me = session.get("user")
    if not me:
        return jsonify({"error": "로그인 필요"}), 401
    set_enabled(me["id"], False)
    return jsonify({"ok": True})


@app.route("/subscribe", methods=["POST"])
def subscribe():
    me = session.get("user")
    if not me:
        return jsonify({"error": "로그인 필요"}), 401
    set_enabled(me["id"], True)
    return jsonify({"ok": True})


@app.route("/settings", methods=["GET", "POST"])
def settings():
    me = session.get("user")
    if not me:
        return redirect(url_for("login"))
    user = get_user(me["id"])
    if not user:
        return redirect(url_for("logout"))
    if request.method == "POST":
        kws = _parse_csv_list(request.form.get("keywords", ""))
        regs = _parse_csv_list(request.form.get("regions", ""))
        set_user_settings(me["id"], kws, regs)
        return redirect(url_for("settings"))
    try:
        kws = json.loads(user.get("custom_keywords") or "[]")
        regs = json.loads(user.get("custom_regions") or "[]")
    except Exception:
        kws, regs = [], []
    return render_template(
        "settings.html",
        me=me,
        keywords=", ".join(kws),
        regions=", ".join(regs),
        default_keywords=crawler.DEFAULT_KEYWORDS,
        default_regions=crawler.DEFAULT_REGIONS,
    )


@app.route("/health")
@app.route("/healthz")
def health():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
        return jsonify({"status": "ok"})
    except Exception as e:
        log.error("health.db_failed", error=str(e))
        return jsonify({"status": "error"}), 503


@app.route("/api/jobs")
def api_jobs():
    me = session.get("user")
    # 신규 파라미터: q(검색어), offset, limit, include_read
    # include_read: 1이면 읽은 공고도 포함 (기본 0 = 안 읽은 것만 / 비로그인은 전체)
    include_read = request.args.get("include_read") == "1"
    q = (request.args.get("q") or "").strip()
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        offset = 0
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 100))

    # SQL 쿼리 구성: ILIKE 로 대소문자 무시 부분 검색
    # 검색 대상: title, org, location, job_type (tags UI에 사용되는 필드)
    where_clauses = []
    params = []
    if q:
        where_clauses.append(
            "(title ILIKE %s OR org ILIKE %s OR location ILIKE %s OR job_type ILIKE %s)"
        )
        pat = f"%{q}%"
        params.extend([pat, pat, pat, pat])
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # 중복 그룹화가 Python 레벨에서 일어나므로, 안전 상한 내에서 모두 가져와
    # 그룹핑 → 필터 → 슬라이싱. 현재 규모(수천 이하)에 적합한 trade-off.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM jobs {where_sql} ORDER BY is_new DESC, crawled_at DESC LIMIT 2000",
                tuple(params),
            )
            rows = cur.fetchall()
    jobs = [dict(r) for r in rows]
    _attach_read_flag(jobs, me)
    jobs = _group_duplicates(jobs)
    if me:
        if not include_read:
            jobs = [j for j in jobs if not j["read"]]
        jobs.sort(key=lambda j: (j["read"], not j.get("is_new")))

    total = len(jobs)
    page = jobs[offset:offset + limit]
    has_more = (offset + limit) < total
    return jsonify({"jobs": page, "has_more": has_more, "next_offset": offset + len(page), "total": total})


@app.route("/api/jobs/<job_id>/read", methods=["POST"])
def api_mark_read(job_id):
    me = session.get("user")
    if not me:
        return jsonify({"error": "로그인 필요"}), 401
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT org, title FROM jobs WHERE id=%s", (job_id,))
            row = cur.fetchone()
            if not row:
                # 존재하지 않는 job_id라도 읽음 마크 자체는 기록 허용
                cur.execute(
                    "INSERT INTO job_reads (kakao_id, job_id, read_at) VALUES (%s, %s, %s) ON CONFLICT (kakao_id, job_id) DO NOTHING",
                    (me["id"], job_id, datetime.now().isoformat()),
                )
                return jsonify({"ok": True})
            target_key = crawler.dedup_key(row["org"], row["title"])
            cur.execute("SELECT id, org, title FROM jobs")
            all_rows = cur.fetchall()
            matching = [
                r["id"] for r in all_rows if crawler.dedup_key(r["org"], r["title"]) == target_key
            ]
            now_iso = datetime.now().isoformat()
            # 배치 INSERT IGNORE 로 N+1 회피
            if matching:
                cur.executemany(
                    "INSERT INTO job_reads (kakao_id, job_id, read_at) VALUES (%s, %s, %s) ON CONFLICT (kakao_id, job_id) DO NOTHING",
                    [(me["id"], jid, now_iso) for jid in matching],
                )
    return jsonify({"ok": True, "marked": len(matching)})


@app.route("/api/stats")
def api_stats():
    me = session.get("user")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM jobs")
            total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM jobs WHERE is_new = TRUE")
            new_ = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM jobs WHERE job_type LIKE %s", ("%정규%",))
            fulltime = cur.fetchone()["c"]
            cur.execute("SELECT * FROM crawl_log ORDER BY id DESC LIMIT 10")
            logs = cur.fetchall()
            unread = None
            if me:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM jobs "
                    "WHERE id NOT IN (SELECT job_id FROM job_reads WHERE kakao_id=%s)",
                    (me["id"],),
                )
                unread = cur.fetchone()["c"]
    return jsonify({
        "total": total, "new": new_, "fulltime": fulltime,
        "unread": unread,
        "recent_logs": [dict(r) for r in logs],
    })


import threading
_crawl_lock = threading.Lock()
_crawl_running = False


def _crawl_bg():
    global _crawl_running
    try:
        run_crawl_and_notify()
    finally:
        _crawl_running = False


@app.route("/api/crawl_now", methods=["POST"])
def api_crawl_now():
    global _crawl_running
    with _crawl_lock:
        if _crawl_running:
            return jsonify({"ok": False, "error": "이미 수집 중입니다"}), 409
        _crawl_running = True
    threading.Thread(target=_crawl_bg, daemon=True).start()
    return jsonify({"ok": True, "started": True})


@app.route("/api/crawl_status")
def api_crawl_status():
    return jsonify({"running": _crawl_running})


# ══════════════════════════════════════════
#  스케줄러
# ══════════════════════════════════════════
def run_crawl_and_notify():
    extra_kw, extra_rg = get_user_customs()
    crawler.EXTRA_KEYWORDS = list(extra_kw)
    crawler.EXTRA_REGIONS = list(extra_rg)
    start_ts = datetime.now()
    log.info("crawl.started")
    try:
        new_jobs = crawler.run_crawl()
    except Exception as e:
        log.exception("crawl.failed", error=str(e))
        return
    finally:
        crawler.EXTRA_KEYWORDS = []
        crawler.EXTRA_REGIONS = []
    duration_ms = int((datetime.now() - start_ts).total_seconds() * 1000)
    log.info("crawl.finished", new_jobs=len(new_jobs), duration_ms=duration_ms)
    if not new_jobs:
        return
    users = get_enabled_users()
    for u in users:
        matched = [j for j in new_jobs if job_matches_user(j, u)]
        if not matched:
            continue
        try:
            kakao_notify.send_new_jobs_for_user(u, matched, on_token_refresh=update_tokens)
        except Exception as e:
            log.warning("notify.failed", user_id=u["kakao_id"], error=str(e))


def start_scheduler():
    sched = BackgroundScheduler(timezone="Asia/Seoul")
    sched.add_job(run_crawl_and_notify, "interval", minutes=CRAWL_INTERVAL_MINUTES, id="crawl")
    sched.start()
    log.info("scheduler.started", interval_min=CRAWL_INTERVAL_MINUTES)


if os.environ.get("DATABASE_URL"):
    # DB 환경변수가 세팅되어 있을 때만 즉시 초기화
    # (일부 tooling/import 단계에서 DB 없이 모듈 로드하는 경우 회피)
    try:
        init_db()
    except Exception as e:
        log.error("db.init_failed", error=str(e))

if os.environ.get("ENABLE_SCHEDULER", "1") == "1" and os.environ.get("DATABASE_URL"):
    start_scheduler()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
