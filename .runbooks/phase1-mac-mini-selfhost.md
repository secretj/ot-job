---
date: 2026-04-14
project: ot-job-tracker
phase: P1 + P2 + P3 (사용자 실행 가이드)
author: infra-agent
scope: Mac mini 자가호스팅 스택 구축 (Docker Compose + Tailscale Funnel)
related:
  - .plans/2026-04-14-mac-mini-selfhost-migration.md
  - .research/2026-04-14-oracle-mariadb-migration.md
responsibility_boundary: |
  이 런북은 infra 담당 영역(Mac mini 시스템 설정, Docker/Tailscale, compose/Dockerfile/bootstrap 스켈레톤)만 포함.
  app.py/crawler.py 수정, SQLite→MariaDB 코드 번역, schema.sql 작성, requirements.txt 편집은 developer 담당 Phase 4에서 처리.
---

# Phase 1~3 런북 — Mac mini 자가호스팅 스택 구축

본 런북은 **사용자(=운영자 본인)가 Mac mini 앞에 앉아 직접 실행**하는 절차다.
코드 포팅(Phase 4)·데이터 이전(Phase 6)·컷오버(Phase 8)는 별도 런북을 따른다.

---

## 1. 사전 준비 체크리스트

### 1.1 하드웨어 / 계정
- [ ] Mac mini 전원 어댑터 상시 연결 (배터리 없음, 정전 대비 UPS는 선택)
- [ ] 유선 이더넷 또는 안정 Wi-Fi, 공유기 DHCP 고정 할당 권장
- [ ] 애플 ID 로그인 완료, 자동 로그인(System Settings > Users & Groups > Automatically log in)
- [ ] 관리자 계정 sudo 권한 확인
- [ ] 외장 볼륨(SSD/HDD) 연결 및 마운트 경로 확인 — 기본 예시 `/Volumes/Backup`
  - `ls /Volumes/` 로 실제 마운트명 확인 후 `.env` `BACKUP_PATH`에 기재
- [ ] 카카오 개발자 콘솔(https://developers.kakao.com/) 본인 애플리케이션 접근 권한 확인

### 1.2 Tailscale 계정
- [ ] https://login.tailscale.com/start 에서 이메일(Google/Microsoft/Apple/Github OAuth 중 택1)로 가입 — **카드 불요, Personal 플랜 무료**
- [ ] Admin Console → Settings → Feature previews → **HTTPS Certificates**, **Funnel** 기능 활성화(필수)
- [ ] 태넷 이름(tailnet) 확인. 예: `tail1234.ts.net`

### 1.3 소프트웨어 (본 런북 Phase 2에서 설치)
- [ ] Homebrew
- [ ] git, rsync (macOS 기본 포함, 버전만 확인)
- [ ] Docker Desktop for Mac (확정 — OrbStack 아님)
- [ ] Tailscale macOS 앱 (App Store 또는 공식 pkg)

---

## 2. Phase 2 — Mac mini 환경 준비

> 모든 명령은 Mac mini의 Terminal.app에서 실행. 사용자 홈 디렉터리 기준 `~/ot-job-tracker`.

### 2.1 Homebrew 및 CLI

```bash
# Homebrew 미설치 시
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# PATH 반영 (Apple Silicon 기준)
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

# 필수 CLI
brew install git rsync
brew install --cask docker          # Docker Desktop for Mac
# Tailscale은 앱스토어 권장(자동 업데이트). CLI만 필요시:
# brew install --cask tailscale
```

설치 후 1회 수동 실행:
- Docker Desktop 실행 → 초기 설정 완료까지 대기(약 2분)
- Settings > General > **Start Docker Desktop when you sign in to your computer** 체크
- Settings > Resources > CPU: 4 core, Memory: 6 GB, Swap: 2 GB, Disk: 64 GB 권장(Mac mini 16GB 기준)
- Settings > Advanced > **Allow the default Docker socket to be used** 체크

예상 출력 확인:
```bash
docker --version          # Docker version 25.x 이상
docker compose version    # Docker Compose version v2.x 이상
```

### 2.2 슬립 / 전원 정책

```bash
sudo pmset -a sleep 0 displaysleep 0 disksleep 0 womp 1 autorestart 1
# autorestart=1: 전원 재연결 시 자동 부팅
# displaysleep=0: 디스플레이 꺼짐도 차단(원하면 30 정도로 완화 가능)

# 확인
pmset -g
```

기대 출력(발췌):
```
 sleep                0
 displaysleep         0
 disksleep            0
 womp                 1
 autorestart          1
```

추가 방어선: 부팅 시 `caffeinate -i -s` 백그라운드 실행 (launchd agent).

`~/Library/LaunchAgents/com.otjob.caffeinate.plist` 작성:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>           <string>com.otjob.caffeinate</string>
    <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-i</string><string>-s</string></array>
    <key>RunAtLoad</key>       <true/>
    <key>KeepAlive</key>       <true/>
</dict>
</plist>
```

로드:
```bash
launchctl load ~/Library/LaunchAgents/com.otjob.caffeinate.plist
```

### 2.3 macOS 방화벽

- System Settings > Network > Firewall → **On**
- Options > **Enable stealth mode** 체크
- Docker.app, Tailscale.app은 "Allow incoming connections"로 자동 등록됨(팝업 승인)
- 외부로부터의 인바운드는 Tailscale 데몬만 허용. 앱은 loopback(`127.0.0.1:8000`) bind만 하므로 외부 노출 없음.

### 2.4 소프트웨어 자동 업데이트 차단 (재부팅 예방)

```bash
sudo softwareupdate --schedule off
```

macOS 업데이트는 **유지보수 윈도우에 수동**으로만 적용.

### 2.5 launchd — 재부팅 시 docker compose up

`~/Library/LaunchAgents/com.otjob.compose.plist` 작성:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>           <string>com.otjob.compose</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-lc</string>
        <string>cd $HOME/ot-job-tracker && /usr/local/bin/docker compose up -d</string>
    </array>
    <key>RunAtLoad</key>       <true/>
    <key>StandardOutPath</key> <string>/tmp/otjob-compose.out.log</string>
    <key>StandardErrorPath</key><string>/tmp/otjob-compose.err.log</string>
</dict>
</plist>
```

Docker Desktop 자체는 "로그인 시 자동 시작"이 켜져 있으므로 Docker 데몬이 뜬 뒤 compose가 기동한다. 필요 시 `sleep 30` 선행.

```bash
launchctl load ~/Library/LaunchAgents/com.otjob.compose.plist
```

---

## 3. Phase 3 — Tailscale Funnel 설정

### 3.1 설치 및 로그인

- App Store에서 Tailscale 설치 → 메뉴바 아이콘 클릭 → Log in → 1.2에서 만든 계정으로 인증
- CLI 연동:
  ```bash
  sudo tailscale up
  tailscale status
  ```
  기대 출력(첫 줄):
  ```
  100.x.y.z    otjob               user@          macOS   -
  ```
- Mac mini 호스트명이 `otjob`이 아니라면 Admin Console > Machines에서 rename **금지**(URL 깨짐). 기본 호스트명을 그대로 쓰되 `.env`에 박제.

FQDN 확인:
```bash
tailscale status --json | jq -r '.Self.DNSName'
# 예: otjob.tail1234.ts.net.
```

### 3.2 HTTPS 인증서 및 Funnel 활성화

Admin Console:
- Access controls(ACL) 에디터에서 다음 블록 추가(이미 있으면 스킵):
  ```json
  {
    "nodeAttrs": [
      { "target": ["*"], "attr": ["funnel"] }
    ]
  }
  ```
- DNS 탭 → **HTTPS Certificates: Enabled**

### 3.3 serve/funnel 설정 (영구)

```bash
# 443 → 127.0.0.1:8000 (앱 컨테이너 loopback bind)
sudo tailscale serve --bg --https=443 --set-path=/ http://127.0.0.1:8000
sudo tailscale funnel --bg 443 on

# 상태 확인
tailscale serve status
tailscale funnel status
```

`tailscale serve` 설정은 `/Library/Tailscale/` 영속 저장소에 기록되어 재부팅 후 자동 복구된다. 별도 `serve.json` 수동 관리 불필요(단, 백업 용도로 `tailscale serve status --json > ~/ot-job-tracker/ops/serve.json` 스냅샷 보관 권장).

### 3.4 외부 검증

**반드시 Mac mini와 같은 네트워크 밖(휴대폰 LTE 테더링 등)에서**:
```bash
curl -i https://otjob.<tailnet>.ts.net/healthz
# HTTP/2 200 (앱이 올라가 있을 때) 또는 502 (앱 미가동)
```

간격 1분으로 3회 연속 200이면 합격.

Grafana(3000)·MariaDB(3306)는 **Funnel에 노출 금지**. Tailscale 내부망으로만 접근: `tailscale serve --https=3000 http://127.0.0.1:3000` 형태로 내부 전용 path를 별도 구성하거나, 그냥 `http://otjob:3000`를 tailnet 내부에서만 접속.

---

## 4. Phase 1 — Docker Compose 스택 파일

> 본 절의 파일들은 **infra가 스켈레톤을 제공**하고 developer가 Phase 4에서 내용물을 채운다.

### 4.1 디렉터리 구조

```
~/ot-job-tracker/
├── docker-compose.yml
├── .env.example
├── .env                    # (git 제외, bootstrap.sh에서 생성)
├── bootstrap.sh
├── Dockerfile              # app/scheduler 공용
├── requirements.txt        # (developer 소관)
├── app.py                  # (developer 소관)
├── crawler.py              # (developer 소관)
├── scheduler.py            # (developer 소관, APScheduler entry)
├── docker/
│   ├── loki/config.yml
│   ├── promtail/config.yml
│   └── grafana/provisioning/
│       ├── datasources/loki.yml
│       └── dashboards/dashboards.yml
├── data/                   # (git 제외, bind mount 대상)
│   ├── mariadb/
│   ├── loki/
│   └── grafana/
├── backups/                # 기본 BACKUP_PATH (외장볼륨으로 심볼릭 링크 권장)
└── ops/
    └── serve.json          # Tailscale serve 설정 스냅샷
```

### 4.2 `docker-compose.yml`

```yaml
# 포터빌리티 원칙: 호스트 절대경로 금지, 전부 상대 경로 또는 ${ENV}
services:
  mariadb:
    image: mariadb:10.11
    restart: unless-stopped
    environment:
      MARIADB_ROOT_PASSWORD: ${DB_ROOT_PASSWORD}
      MARIADB_DATABASE: ${DB_NAME:-otjob}
      MARIADB_USER: ${DB_USER:-otjob}
      MARIADB_PASSWORD: ${DB_PASSWORD}
      TZ: ${TZ:-Asia/Seoul}
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
    volumes:
      - mariadb_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 10
    # 외부 노출 없음, compose 네트워크 내부에서만 접근

  app:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    depends_on:
      mariadb:
        condition: service_healthy
    environment:
      DB_HOST: mariadb
      DB_PORT: 3306
      DB_NAME: ${DB_NAME:-otjob}
      DB_USER: ${DB_USER:-otjob}
      DB_PASSWORD: ${DB_PASSWORD}
      KAKAO_CLIENT_ID: ${KAKAO_CLIENT_ID}
      KAKAO_CLIENT_SECRET: ${KAKAO_CLIENT_SECRET}
      PUBLIC_BASE_URL: ${PUBLIC_BASE_URL}
      TZ: ${TZ:-Asia/Seoul}
    command: ["gunicorn", "-b", "0.0.0.0:8000", "-w", "2", "app:app"]
    ports:
      - "127.0.0.1:8000:8000"    # loopback bind, Tailscale Funnel이 TLS 종단
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/healthz"]
      interval: 30s
      timeout: 5s
      retries: 5

  scheduler:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    depends_on:
      mariadb:
        condition: service_healthy
    environment:
      DB_HOST: mariadb
      DB_PORT: 3306
      DB_NAME: ${DB_NAME:-otjob}
      DB_USER: ${DB_USER:-otjob}
      DB_PASSWORD: ${DB_PASSWORD}
      TZ: ${TZ:-Asia/Seoul}
    command: ["python", "scheduler.py"]
    # single-instance 보장: replicas 지정 금지

  loki:
    image: grafana/loki:2.9.0
    restart: unless-stopped
    command: -config.file=/etc/loki/config.yml
    volumes:
      - ./docker/loki/config.yml:/etc/loki/config.yml:ro
      - loki_data:/loki

  promtail:
    image: grafana/promtail:2.9.0
    restart: unless-stopped
    command: -config.file=/etc/promtail/config.yml
    volumes:
      - ./docker/promtail/config.yml:/etc/promtail/config.yml:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
    depends_on:
      - loki

  grafana:
    image: grafana/grafana:10.4.0
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_USERS_ALLOW_SIGN_UP: "false"
      TZ: ${TZ:-Asia/Seoul}
    volumes:
      - grafana_data:/var/lib/grafana
      - ./docker/grafana/provisioning:/etc/grafana/provisioning:ro
    ports:
      - "127.0.0.1:3000:3000"    # Funnel 비노출, tailnet 내부 전용
    depends_on:
      - loki

volumes:
  mariadb_data:
  loki_data:
  grafana_data:
```

> **백업 bind mount**는 `scripts/backup.sh`에서 `${BACKUP_PATH:-./backups}`를 참조. compose 서비스로는 포함하지 않음(호스트 cron/launchd에서 `docker exec`).

### 4.3 `.env.example`

```bash
# === Database ===
DB_NAME=otjob
DB_USER=otjob
DB_PASSWORD=change-me-strong-password
DB_ROOT_PASSWORD=change-me-root-password

# === Kakao OAuth (developer가 실값 주입) ===
KAKAO_CLIENT_ID=
KAKAO_CLIENT_SECRET=

# === Public URL (Phase 3 Tailscale Funnel 확정 후 기록) ===
PUBLIC_BASE_URL=https://otjob.<tailnet>.ts.net

# === Grafana ===
GRAFANA_ADMIN_PASSWORD=change-me-grafana

# === 공통 ===
TZ=Asia/Seoul

# === 백업 외장볼륨 ===
BACKUP_PATH=/Volumes/Backup/ot-job-tracker
```

`.gitignore`에 `.env`, `data/`, `backups/` 추가.

### 4.4 `Dockerfile` (app/scheduler 공용)

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# CMD는 compose에서 오버라이드 (app=gunicorn / scheduler=python scheduler.py)
CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:app"]
```

### 4.5 `bootstrap.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  if [ ! -f .env.example ]; then
    echo "ERROR: .env.example not found" >&2
    exit 1
  fi
  cp .env.example .env
  echo ".env 생성됨. 값 편집 후 다시 실행하세요."
  exit 2
fi

# 필수 키 검증
required=(DB_PASSWORD DB_ROOT_PASSWORD KAKAO_CLIENT_ID KAKAO_CLIENT_SECRET PUBLIC_BASE_URL GRAFANA_ADMIN_PASSWORD)
missing=()
for key in "${required[@]}"; do
  val=$(grep -E "^${key}=" .env | cut -d= -f2-)
  if [ -z "$val" ] || [[ "$val" == change-me* ]]; then
    missing+=("$key")
  fi
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "ERROR: .env 미설정 키: ${missing[*]}" >&2
  exit 3
fi

# 데이터 디렉터리
mkdir -p data/mariadb data/loki data/grafana backups

# 외장 백업 경로 검증(있으면 bind, 없으면 로컬 ./backups)
BACKUP_PATH=$(grep -E "^BACKUP_PATH=" .env | cut -d= -f2-)
if [ -n "${BACKUP_PATH}" ] && [ ! -d "${BACKUP_PATH}" ]; then
  echo "WARN: BACKUP_PATH(${BACKUP_PATH}) 미존재 — 외장볼륨 마운트 확인 후 백업 스크립트 재점검 필요"
fi

docker compose pull
docker compose up -d --build

echo ""
echo "=== 서비스 상태 ==="
docker compose ps
echo ""
echo "healthcheck 대기 (최대 90초)..."
for i in {1..18}; do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "OK: app healthy"
    exit 0
  fi
  sleep 5
done
echo "WARN: app healthcheck 미성공. docker compose logs app --tail=100 확인."
exit 4
```

```bash
chmod +x bootstrap.sh
```

### 4.6 Loki / Promtail / Grafana 설정 스켈레톤

`docker/loki/config.yml`:
```yaml
auth_enabled: false
server:
  http_listen_port: 3100
common:
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
limits_config:
  retention_period: 168h     # 7일
```

`docker/promtail/config.yml`:
```yaml
server:
  http_listen_port: 9080
positions:
  filename: /tmp/positions.yaml
clients:
  - url: http://loki:3100/loki/api/v1/push
scrape_configs:
  - job_name: docker
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 15s
    relabel_configs:
      - source_labels: [__meta_docker_container_name]
        target_label: container
      - source_labels: [__meta_docker_container_log_stream]
        target_label: stream
```

`docker/grafana/provisioning/datasources/loki.yml`:
```yaml
apiVersion: 1
datasources:
  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
    isDefault: true
```

`docker/grafana/provisioning/dashboards/dashboards.yml`:
```yaml
apiVersion: 1
providers:
  - name: default
    folder: ''
    type: file
    disableDeletion: false
    options:
      path: /etc/grafana/provisioning/dashboards
```

### 4.7 포터빌리티 체크

- [ ] `docker-compose.yml` 내 경로는 모두 상대(`./docker/...`) 또는 named volume
- [ ] macOS 전용 구문 없음(bind는 docker.sock 하나, Linux도 동일 경로)
- [ ] Dockerfile linux/amd64 · linux/arm64 모두 빌드 가능(python:3.11-slim 멀티아키)
- [ ] `.env.example`만 git 추적, `.env`와 `data/`는 무시
- [ ] 이전 시: `rsync -av ~/ot-job-tracker/ new-host:~/ot-job-tracker/` + `./bootstrap.sh` 한 번

---

## 5. 카카오 OAuth Redirect URI 업데이트

### 5.1 신규 URI 추가 (Phase 7, 컷오버 전)

1. https://developers.kakao.com/ 로그인
2. 내 애플리케이션 → 해당 앱 선택
3. 제품 설정 → 카카오 로그인 → Redirect URI → **수정**
4. 기존 Fly URI 유지한 채, 다음 줄 **추가**:
   ```
   https://otjob.<tailnet>.ts.net/auth/kakao/callback
   ```
5. 저장 → 반영까지 최대 1분 대기

### 5.2 기본 URI 승격 (Phase 8 컷오버 T+15m)

- 개발자 콘솔에서 Tailscale URI를 기본(최상단)으로 이동, Fly URI는 롤백용 **유지**.

### 5.3 Fly URI 제거 (컷오버 T+3일)

- 롤백 없이 안정 운영 72시간 경과 확인 후 Fly URI 삭제.
- 삭제 전 Grafana에서 `/auth/kakao/callback` 경로 에러율 0 확인.

---

## 6. 검증 커맨드

```bash
# 1) Compose 상태
docker compose ps
#   전 서비스 STATUS=running, HEALTH=healthy (mariadb/app)

# 2) 앱 로그
docker compose logs app --tail=50
#   ERROR/Traceback 없음

# 3) 스케줄러 로그
docker compose logs scheduler --tail=50
#   APScheduler 'started' 출력, 다음 tick 시각 표시

# 4) 로컬 healthcheck
curl -fsS http://127.0.0.1:8000/healthz
#   {"status":"ok"} 또는 developer 구현 포맷

# 5) Tailscale 외부 healthcheck (반드시 외부망)
curl -i https://otjob.<tailnet>.ts.net/healthz
#   HTTP/2 200

# 6) Tailscale Funnel 상태
tailscale funnel status
tailscale serve status

# 7) Grafana 접속 (tailnet 내부 전용)
open http://127.0.0.1:3000    # Mac mini 로컬
# 또는 다른 tailnet 기기에서 http://otjob:3000

# 8) DB 접속 확인 (컨테이너 내부)
docker compose exec mariadb mariadb -u otjob -p otjob -e "SHOW TABLES;"
```

---

## 7. 트러블슈팅

| 증상 | 원인 후보 | 조치 |
|---|---|---|
| Mac mini 새벽에 슬립 진입 | pmset 설정 누락 또는 `powerd` 이슈 | `pmset -g` 재확인, `caffeinate` launchd agent 로드 상태 점검 `launchctl list | grep caffeinate` |
| `tailscale funnel` 403/HTTP 없음 | Admin ACL funnel attr 미등록, HTTPS cert 미활성 | Admin Console → ACL에 `nodeAttrs` funnel 추가, DNS > HTTPS Certificates Enable |
| 재부팅 후 compose 미기동 | Docker Desktop 시작 지연 | launchd plist에 `sleep 30` 선행 또는 Docker Desktop "로그인 시 자동 시작" 확인 |
| 카카오 로그인 `KOE006` | redirect URI 불일치 | 개발자 콘솔 등록값과 `PUBLIC_BASE_URL + /auth/kakao/callback` 완전 일치 확인(스킴·슬래시·포트) |
| 외장볼륨 언마운트로 백업 실패 | 슬립/재부팅 후 미자동마운트 | `scripts/backup.sh` 선두에 `mountpoint -q "${BACKUP_PATH}" || { echo "NOT MOUNTED"; exit 1; }` (macOS는 `mount | grep` 대체) + UptimeRobot heartbeat 연결 |
| mariadb 컨테이너 permission denied | bind mount UID 불일치 | named volume(`mariadb_data`) 사용 중이면 무관. bind로 바꿨다면 `user:` 필드 추가 |
| Docker Desktop 메모리 스왑 폭주 | 리소스 할당 과다 | Settings > Resources에서 Memory 6GB로 제한 |
| Funnel 대역폭/용도 위반 경고 | Tailscale 정책 | 정적 대용량 전송 금지. 필요시 Cloudflare Tunnel 병행 과제화(Phase 9 이후) |

---

## 8. 완료 기준 (Phase 4 진입 gate)

- [ ] `docker compose ps`에 6개 서비스(`mariadb`, `app`, `scheduler`, `loki`, `promtail`, `grafana`) 모두 `running`
- [ ] `mariadb`, `app` healthcheck `healthy`
- [ ] 외부 LTE망에서 `curl -i https://otjob.<tailnet>.ts.net/healthz` 3회 연속 200
- [ ] `tailscale serve status` 재부팅 후에도 자동 복구 확인(1회 수동 재부팅 테스트)
- [ ] 외장볼륨 `BACKUP_PATH` 마운트 확인 + `rsync -av --dry-run ./data/mariadb/ ${BACKUP_PATH}/rehearsal/` 리허설 1회 성공
- [ ] 카카오 개발자 콘솔에 Tailscale redirect URI 추가 완료 (Fly URI 병존)
- [ ] `./bootstrap.sh`를 데이터 디렉터리 삭제 후 재실행해도 전 스택 기동 성공(포터빌리티 스모크)

gate 통과 시 PM에게 보고 → Phase 4(developer, 코드 포팅)로 이관.

---

## 9. 롤백 절차 (이 런북 범위 한정)

- 전체 중단: `cd ~/ot-job-tracker && docker compose down`
- 데이터까지 초기화: `docker compose down -v && rm -rf data/` (되돌릴 수 없음, 주의)
- Tailscale Funnel 노출 차단: `sudo tailscale funnel --https=443 off`
- pmset 기본값 복원: `sudo pmset -a sleep 1 displaysleep 10 disksleep 10 womp 0 autorestart 0`
- launchd agent 제거: `launchctl unload ~/Library/LaunchAgents/com.otjob.*.plist && rm ~/Library/LaunchAgents/com.otjob.*.plist`
- 카카오 redirect URI는 Fly URI가 남아있으므로 롤백 시 Fly 앱은 정상 동작.
