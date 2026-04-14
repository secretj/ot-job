FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

WORKDIR /app

# lxml 런타임은 wheel이 slim에도 존재하므로 컴파일러 불필요.
# curl은 healthcheck 및 디버깅 편의용.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r app && useradd -r -g app -u 1000 -d /app app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# compose 에서 app=gunicorn, scheduler=python scheduler_main 오버라이드.
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "2", "app:app"]
