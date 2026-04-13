#!/usr/bin/env python3
"""
OT 채용 트래커 - 멀티유저 웹앱 (Flask + Kakao OAuth + 크롤러 스케줄러)
"""
import os
import json
import secrets
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, request, redirect, session, render_template, jsonify, url_for
from apscheduler.schedulers.background import BackgroundScheduler

import crawler
import kakao_notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

# ── 환경변수 ──
KAKAO_REST_API_KEY = os.environ["KAKAO_REST_API_KEY"]
KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")
KAKAO_REDIRECT_URI = os.environ["KAKAO_REDIRECT_URI"]
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
DB_PATH = os.environ.get("DB_PATH", "/data/jobs.db")
CRAWL_INTERVAL_MINUTES = int(os.environ.get("CRAWL_INTERVAL_MINUTES", "30"))

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
crawler.DB_PATH = Path(DB_PATH)

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


# ══════════════════════════════════════════
#  DB - users 테이블
# ══════════════════════════════════════════
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_users_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            kakao_id INTEGER PRIMARY KEY,
            nickname TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            custom_keywords TEXT DEFAULT '[]',
            custom_regions TEXT DEFAULT '[]'
        )
    """)
    # 기존 DB 마이그레이션
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "custom_keywords" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN custom_keywords TEXT DEFAULT '[]'")
    if "custom_regions" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN custom_regions TEXT DEFAULT '[]'")
    conn.commit()
    conn.close()


def _parse_csv_list(raw):
    """콤마/줄바꿈 구분된 문자열 → 정제된 리스트."""
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
    conn = db()
    rows = conn.execute("SELECT custom_keywords, custom_regions FROM users WHERE enabled=1").fetchall()
    conn.close()
    kws, regs = set(), set()
    for r in rows:
        try:
            kws.update(json.loads(r["custom_keywords"] or "[]"))
            regs.update(json.loads(r["custom_regions"] or "[]"))
        except Exception:
            pass
    return sorted(kws), sorted(regs)


def set_user_settings(kakao_id, keywords, regions):
    conn = db()
    conn.execute(
        "UPDATE users SET custom_keywords=?, custom_regions=? WHERE kakao_id=?",
        (json.dumps(keywords, ensure_ascii=False), json.dumps(regions, ensure_ascii=False), kakao_id),
    )
    conn.commit()
    conn.close()


def get_user(kakao_id):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE kakao_id=?", (kakao_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def job_matches_user(job, user):
    """유저의 (default ∪ custom) 키워드·지역에 해당하면 True."""
    try:
        user_kw = json.loads(user.get("custom_keywords") or "[]")
        user_rg = json.loads(user.get("custom_regions") or "[]")
    except Exception:
        user_kw, user_rg = [], []
    kws = crawler.DEFAULT_KEYWORDS + user_kw
    regs = crawler.DEFAULT_REGIONS + user_rg
    full_text = f"{job.get('title','')} {job.get('org','')} {job.get('location','')}"
    # 키워드 매칭 필수
    if not any(k in full_text for k in kws):
        return False
    # 지역: location이 "전국/미상"이면 통과 (게시판류), 아니면 매칭 확인
    loc = job.get("location", "")
    if loc and "전국" not in loc and "미상" not in loc:
        if not any(r in (loc + " " + full_text) for r in regs):
            return False
    return True


def upsert_user(kakao_id, nickname, access_token, refresh_token, expires_at):
    conn = db()
    conn.execute("""
        INSERT INTO users (kakao_id, nickname, access_token, refresh_token, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(kakao_id) DO UPDATE SET
            nickname=excluded.nickname,
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token,
            expires_at=excluded.expires_at
    """, (kakao_id, nickname, access_token, refresh_token, expires_at, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_enabled_users():
    conn = db()
    rows = conn.execute("SELECT * FROM users WHERE enabled = 1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_enabled(kakao_id, enabled):
    conn = db()
    conn.execute("UPDATE users SET enabled = ? WHERE kakao_id = ?", (1 if enabled else 0, kakao_id))
    conn.commit()
    conn.close()


def update_tokens(kakao_id, access_token, refresh_token, expires_at):
    conn = db()
    conn.execute(
        "UPDATE users SET access_token=?, refresh_token=?, expires_at=? WHERE kakao_id=?",
        (access_token, refresh_token, expires_at, kakao_id),
    )
    conn.commit()
    conn.close()


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
@app.route("/")
def index():
    me = session.get("user")
    conn = db()
    jobs = conn.execute(
        "SELECT * FROM jobs ORDER BY is_new DESC, crawled_at DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return render_template("index.html", jobs=[dict(j) for j in jobs], me=me)


@app.route("/login")
def login():
    return redirect(kakao_auth_url())


@app.route("/kakao/callback")
def kakao_callback():
    code = request.args.get("code")
    if not code:
        return "인증 실패: code 없음", 400
    try:
        tok = exchange_code(code)
        profile = fetch_profile(tok["access_token"])
    except requests.HTTPError as e:
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
def health():
    return "ok"


@app.route("/api/jobs")
def api_jobs():
    keyword = request.args.get("keyword", "")
    conn = db()
    if keyword:
        q = f"%{keyword}%"
        rows = conn.execute(
            "SELECT * FROM jobs WHERE title LIKE ? OR org LIKE ? OR location LIKE ? OR job_type LIKE ? ORDER BY is_new DESC, crawled_at DESC LIMIT 200",
            (q, q, q, q),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM jobs ORDER BY is_new DESC, crawled_at DESC LIMIT 200").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/stats")
def api_stats():
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    new_ = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_new=1").fetchone()[0]
    fulltime = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_type LIKE '%정규%'").fetchone()[0]
    logs = conn.execute("SELECT * FROM crawl_log ORDER BY id DESC LIMIT 10").fetchall()
    conn.close()
    return jsonify({
        "total": total, "new": new_, "fulltime": fulltime,
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
    try:
        new_jobs = crawler.run_crawl()
    except Exception as e:
        log.exception(f"크롤링 실패: {e}")
        return
    finally:
        crawler.EXTRA_KEYWORDS = []
        crawler.EXTRA_REGIONS = []
    if not new_jobs:
        return
    users = get_enabled_users()
    log.info(f"신규 {len(new_jobs)}건 → {len(users)}명 대상 필터링/발송")
    for u in users:
        matched = [j for j in new_jobs if job_matches_user(j, u)]
        if not matched:
            continue
        try:
            kakao_notify.send_new_jobs_for_user(u, matched, on_token_refresh=update_tokens)
        except Exception as e:
            log.warning(f"발송 실패 user={u['kakao_id']}: {e}")


def start_scheduler():
    sched = BackgroundScheduler(timezone="Asia/Seoul")
    sched.add_job(run_crawl_and_notify, "interval", minutes=CRAWL_INTERVAL_MINUTES, id="crawl")
    sched.start()
    log.info(f"스케줄러 시작: {CRAWL_INTERVAL_MINUTES}분 간격")


init_users_db()
crawler.init_db()  # jobs, crawl_log 테이블 생성
if os.environ.get("ENABLE_SCHEDULER", "1") == "1":
    start_scheduler()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
