#!/usr/bin/env python3
"""
카카오톡 나에게 보내기 - 새 공고 알림
"""

import json
import logging
from pathlib import Path
import requests

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
TOKEN_PATH = BASE_DIR / "kakao_token.json"

log = logging.getLogger("kakao")


def load_token():
    if not TOKEN_PATH.exists():
        raise FileNotFoundError("kakao_token.json 없음. python3 kakao_auth.py 먼저 실행하세요.")
    return json.loads(TOKEN_PATH.read_text())


def refresh_token():
    """리프레시 토큰으로 액세스 토큰 갱신"""
    config = json.loads(CONFIG_PATH.read_text())
    token_data = load_token()
    refresh = token_data.get("refresh_token")
    if not refresh:
        raise ValueError("refresh_token이 없습니다. kakao_auth.py를 다시 실행하세요.")

    r = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": config["kakao_rest_api_key"],
            "refresh_token": refresh,
        },
    )
    r.raise_for_status()
    new_data = r.json()

    # 기존 토큰에 업데이트
    token_data["access_token"] = new_data["access_token"]
    if "refresh_token" in new_data:
        token_data["refresh_token"] = new_data["refresh_token"]
    TOKEN_PATH.write_text(json.dumps(token_data, indent=2, ensure_ascii=False))
    log.info("카카오 토큰 갱신 완료")
    return token_data


def send_to_me(text):
    """카카오톡 나에게 보내기 (텍스트)"""
    token_data = load_token()
    access_token = token_data["access_token"]

    template = {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": "http://localhost:5050",
            "mobile_web_url": "http://localhost:5050",
        },
        "button_title": "대시보드 열기",
    }

    r = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template)},
    )

    if r.status_code == 401:
        # 토큰 만료 → 갱신 후 재시도
        log.info("토큰 만료, 갱신 중...")
        token_data = refresh_token()
        access_token = token_data["access_token"]
        r = requests.post(
            "https://kapi.kakao.com/v2/api/talk/memo/default/send",
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": json.dumps(template)},
        )

    if r.status_code == 200:
        log.info("✅ 카카오톡 전송 성공")
    else:
        log.error(f"❌ 카카오톡 전송 실패: {r.status_code} {r.text}")

    return r.status_code == 200


def send_new_jobs_kakao(jobs):
    """새 공고 목록을 카카오톡으로 전송"""
    if not jobs:
        return

    lines = [f"🔔 OT 채용 새 공고 {len(jobs)}건!\n"]

    for i, job in enumerate(jobs[:5], 1):  # 최대 5건
        loc = job.get("location", "")
        src = job.get("source", "")
        lines.append(f"{i}. [{src}] {job['title']}")
        if loc:
            lines.append(f"   📍 {loc}")
        lines.append("")

    if len(jobs) > 5:
        lines.append(f"... 외 {len(jobs) - 5}건 더")

    lines.append("\n👉 http://localhost:5050 에서 전체 확인")

    text = "\n".join(lines)

    try:
        send_to_me(text)
    except FileNotFoundError:
        log.warning("카카오 토큰 없음 — 알림 건너뜀 (kakao_auth.py 실행 필요)")
    except Exception as e:
        log.error(f"카카오 알림 에러: {e}")


if __name__ == "__main__":
    # 테스트
    logging.basicConfig(level=logging.INFO)
    send_to_me("🔔 OT 채용 트래커 테스트 메시지입니다!")
