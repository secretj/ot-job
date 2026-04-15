-- ot-job-tracker PostgreSQL 스키마 (Neon)
-- bootstrap 시 idempotent 하게 재실행 가능.

CREATE TABLE IF NOT EXISTS users (
    kakao_id        BIGINT       PRIMARY KEY,
    nickname        VARCHAR(255),
    access_token    VARCHAR(512),
    refresh_token   VARCHAR(512),
    expires_at      VARCHAR(32),
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      VARCHAR(32),
    custom_keywords TEXT         NOT NULL,
    custom_regions  TEXT         NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_enabled ON users (enabled);

CREATE TABLE IF NOT EXISTS jobs (
    id          VARCHAR(16)  PRIMARY KEY,
    source      VARCHAR(64),
    title       VARCHAR(512),
    org         VARCHAR(255),
    location    VARCHAR(255),
    job_type    VARCHAR(64),
    deadline    VARCHAR(64),
    url         VARCHAR(1024),
    crawled_at  VARCHAR(32),
    is_new      BOOLEAN      NOT NULL DEFAULT TRUE,
    notified    BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_jobs_is_new_crawled ON jobs (is_new, crawled_at);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs (source);

CREATE TABLE IF NOT EXISTS job_reads (
    kakao_id BIGINT      NOT NULL REFERENCES users(kakao_id) ON DELETE CASCADE,
    job_id   VARCHAR(16) NOT NULL REFERENCES jobs(id)       ON DELETE CASCADE,
    read_at  VARCHAR(32) NOT NULL,
    PRIMARY KEY (kakao_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_reads_user ON job_reads (kakao_id);
CREATE INDEX IF NOT EXISTS idx_reads_job  ON job_reads (job_id);

CREATE TABLE IF NOT EXISTS crawl_log (
    id         BIGSERIAL    PRIMARY KEY,
    timestamp  VARCHAR(32),
    source     VARCHAR(64),
    found      INTEGER,
    new_count  INTEGER,
    status     VARCHAR(255)
);
CREATE INDEX IF NOT EXISTS idx_crawl_log_source ON crawl_log (source);
CREATE INDEX IF NOT EXISTS idx_crawl_log_time ON crawl_log (timestamp);
