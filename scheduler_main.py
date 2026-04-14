#!/usr/bin/env python3
"""
scheduler 컨테이너 엔트리포인트.

app 프로세스의 BackgroundScheduler는 ENABLE_SCHEDULER=0 으로 비활성화하고,
이 독립 프로세스가 APScheduler를 싱글 인스턴스로 돌린다.
이렇게 하면 gunicorn worker 수에 관계없이 크롤 중복 실행이 없다.
"""
from __future__ import annotations

import os
import signal
import sys
import time

# scheduler 컨테이너는 앱의 스케줄러 포크를 원치 않으므로 반드시 disable
os.environ.setdefault("ENABLE_SCHEDULER", "0")

from apscheduler.schedulers.blocking import BlockingScheduler

from logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger("scheduler")


def main() -> int:
    # app 모듈 로드 시점에 init_db() + 환경변수 검증됨
    import app as app_mod  # noqa: F401  (side effect: init_db)

    interval = int(os.environ.get("CRAWL_INTERVAL_MINUTES", "30"))
    sched = BlockingScheduler(timezone="Asia/Seoul")
    sched.add_job(
        app_mod.run_crawl_and_notify,
        "interval",
        minutes=interval,
        id="crawl",
        next_run_time=None,  # 첫 즉시 실행 원하면 datetime.now() 지정
    )
    log.info("scheduler.started", interval_min=interval, mode="standalone")

    def _graceful(signum, _frame):
        log.info("scheduler.stopping", signal=signum)
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
