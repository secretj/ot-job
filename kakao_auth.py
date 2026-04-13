#!/usr/bin/env python3
"""
카카오톡 나에게 보내기 - 최초 인증 (1회만 실행)
브라우저에서 카카오 로그인 → 동의 → 토큰 자동 저장
"""

import json
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
TOKEN_PATH = BASE_DIR / "kakao_token.json"

config = json.loads(CONFIG_PATH.read_text())
REST_API_KEY = config["kakao_rest_api_key"]
REDIRECT_URI = config["kakao_redirect_uri"]
CLIENT_SECRET = config.get("kakao_client_secret")

auth_code = None

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)

        if parsed.path == "/kakao/callback":
            params = parse_qs(parsed.query)
            auth_code = params.get("code", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("""
                <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
                <h1>✅ 카카오 인증 완료!</h1>
                <p>이 창을 닫아도 됩니다.</p>
                </body></html>
            """.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # 로그 숨김


def get_token(code):
    """인가 코드로 액세스 토큰 발급"""
    data = {
        "grant_type": "authorization_code",
        "client_id": REST_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
    if CLIENT_SECRET:
        data["client_secret"] = CLIENT_SECRET
    r = requests.post("https://kauth.kakao.com/oauth/token", data=data)
    r.raise_for_status()
    return r.json()


def main():
    # 1. 카카오 로그인 페이지 열기
    auth_url = (
        f"https://kauth.kakao.com/oauth/authorize"
        f"?client_id={REST_API_KEY}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=talk_message"
    )
    print("🔗 브라우저에서 카카오 로그인 페이지를 엽니다...")
    webbrowser.open(auth_url)

    # 2. 콜백 대기
    print("⏳ 카카오 로그인 대기 중... (브라우저에서 로그인해주세요)")
    server = HTTPServer(("localhost", 5050), CallbackHandler)
    while auth_code is None:
        server.handle_request()

    print(f"✅ 인가 코드 수신: {auth_code[:20]}...")

    # 3. 토큰 발급
    token_data = get_token(auth_code)
    TOKEN_PATH.write_text(json.dumps(token_data, indent=2, ensure_ascii=False))
    print(f"✅ 토큰 저장 완료: {TOKEN_PATH}")
    print(f"   access_token: {token_data['access_token'][:20]}...")
    print(f"   expires_in: {token_data.get('expires_in', '?')}초")
    print()
    print("🎉 설정 완료! 이제 crawler.py를 실행하면 카카오톡으로 알림이 옵니다.")


if __name__ == "__main__":
    main()
