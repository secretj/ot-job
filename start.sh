#!/bin/bash
# OT 채용 트래커 - 원클릭 실행
# 크롤러(백그라운드) + 웹서버 동시 실행

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=========================================="
echo "  🩺 OT 채용 트래커 시작"
echo "  서울 · 작업치료 · 감각통합"
echo "=========================================="
echo ""

# Python 확인
if ! command -v python3 &> /dev/null; then
    echo "❌ python3이 설치되어 있지 않습니다."
    echo "   brew install python3 으로 설치해주세요."
    exit 1
fi

# 패키지 확인
echo "📦 패키지 확인 중..."
pip3 install -q -r requirements.txt 2>/dev/null

# 카카오 토큰 확인
if [ ! -f "kakao_token.json" ]; then
    echo ""
    echo "⚠️  카카오톡 인증이 필요합니다."
    echo "   config.json에 REST API 키를 설정한 후"
    echo "   python3 kakao_auth.py 를 먼저 실행해주세요."
    echo ""
    echo "   카카오 없이 웹 대시보드만 사용할 수도 있습니다."
    echo ""
fi

# 크롤러 백그라운드 실행
echo "🔄 크롤러 시작 (백그라운드)..."
python3 crawler.py &
CRAWLER_PID=$!
echo "   PID: $CRAWLER_PID"

# 웹서버 실행
echo "🌐 웹 대시보드: http://localhost:5050"
echo ""
echo "종료하려면 Ctrl+C"
echo ""

# 종료 시 크롤러도 같이 종료
trap "echo ''; echo '🛑 종료 중...'; kill $CRAWLER_PID 2>/dev/null; exit 0" SIGINT SIGTERM

python3 web_server.py
