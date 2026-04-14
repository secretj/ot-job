#!/usr/bin/env python3
"""
카카오톡 나에게 보내기 - 멀티유저
"""
import os
import json
from datetime import datetime, timedelta

import requests

from logging_setup import get_logger

log = get_logger("kakao_notify")

KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "")
KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")


def refresh_access_token(refresh_token):
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "refresh_token": refresh_token,
    }
    if KAKAO_CLIENT_SECRET:
        data["client_secret"] = KAKAO_CLIENT_SECRET
    r = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=10)
    r.raise_for_status()
    return r.json()


def ensure_fresh_token(user, on_token_refresh=None):
    expires_at = user.get("expires_at")
    try:
        if expires_at and datetime.fromisoformat(expires_at) > datetime.now() + timedelta(minutes=5):
            return user["access_token"]
    except Exception:
        pass

    tok = refresh_access_token(user["refresh_token"])
    new_access = tok["access_token"]
    new_refresh = tok.get("refresh_token", user["refresh_token"])
    new_expires = (datetime.now() + timedelta(seconds=int(tok.get("expires_in", 21600)) - 60)).isoformat()
    if on_token_refresh:
        on_token_refresh(user["kakao_id"], new_access, new_refresh, new_expires)
    user["access_token"] = new_access
    user["refresh_token"] = new_refresh
    user["expires_at"] = new_expires
    return new_access


def send_memo(access_token, template_object):
    r = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template_object, ensure_ascii=False)},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def build_text(new_jobs, base_url=""):
    lines = [f"🩺 새 채용공고 {len(new_jobs)}건"]
    for j in new_jobs[:10]:
        lines.append(f"• [{j['source']}] {j['title']} ({j.get('org', '')})")
    if len(new_jobs) > 10:
        lines.append(f"… 외 {len(new_jobs) - 10}건")
    if base_url:
        lines.append(f"\n👉 {base_url}")
    return "\n".join(lines)


def send_new_jobs_for_user(user, new_jobs, on_token_refresh=None, base_url=None):
    if not base_url:
        base_url = os.environ.get("PUBLIC_URL", "")
    access = ensure_fresh_token(user, on_token_refresh=on_token_refresh)
    text = build_text(new_jobs, base_url)
    first = new_jobs[0]
    template = {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": first.get("url") or base_url or "https://kakao.com",
            "mobile_web_url": first.get("url") or base_url or "https://kakao.com",
        },
        "button_title": "자세히 보기",
    }
    send_memo(access, template)
    log.info("notify.sent", user_id=user["kakao_id"], nickname=user.get("nickname"))
