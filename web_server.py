#!/usr/bin/env python3
"""
OT 채용 트래커 - 웹 대시보드
http://localhost:5050 에서 공고 목록 확인
"""

import json
import sqlite3
from pathlib import Path
from flask import Flask, render_template, jsonify, request

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "jobs.db"

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/jobs")
def api_jobs():
    """공고 목록 API"""
    source = request.args.get("source", "")
    keyword = request.args.get("keyword", "")
    job_type = request.args.get("type", "")

    conn = get_db()
    query = "SELECT * FROM jobs WHERE 1=1"
    params = []

    if source:
        query += " AND source = ?"
        params.append(source)
    if keyword:
        query += " AND (title LIKE ? OR org LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if job_type:
        query += " AND job_type LIKE ?"
        params.append(f"%{job_type}%")

    query += " ORDER BY crawled_at DESC LIMIT 200"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return jsonify([dict(r) for r in rows])


@app.route("/api/stats")
def api_stats():
    """통계 API"""
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    new_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_new = 1").fetchone()[0]
    fulltime = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_type LIKE '%정규직%'").fetchone()[0]
    sources = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM jobs GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    recent_logs = conn.execute(
        "SELECT * FROM crawl_log ORDER BY id DESC LIMIT 20"
    ).fetchall()

    conn.close()

    return jsonify({
        "total": total,
        "new": new_count,
        "fulltime": fulltime,
        "sources": [dict(s) for s in sources],
        "recent_logs": [dict(l) for l in recent_logs],
    })


@app.route("/api/mark_seen", methods=["POST"])
def mark_seen():
    """모든 공고 읽음 처리"""
    conn = get_db()
    conn.execute("UPDATE jobs SET is_new = 0")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/crawl_now", methods=["POST"])
def crawl_now():
    """수동 크롤링 트리거"""
    try:
        from crawler import run_once
        new_jobs = run_once()
        return jsonify({"ok": True, "new_count": len(new_jobs)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# 카카오 인증 콜백 (kakao_auth.py가 처리하지만, 웹서버에서도 받을 수 있도록)
@app.route("/kakao/callback")
def kakao_callback():
    code = request.args.get("code", "")
    return f"""
    <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
    <h1>✅ 카카오 인증 코드 수신</h1>
    <p>kakao_auth.py에서 처리됩니다.</p>
    <p>코드: {code[:20]}...</p>
    </body></html>
    """


if __name__ == "__main__":
    print("🌐 웹 대시보드: http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
