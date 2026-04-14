-- ot-job-tracker MariaDB 10.x 스키마
-- utf8mb4/utf8mb4_unicode_ci 기본.
-- bootstrap 시 idempotent 하게 재실행 가능(IF NOT EXISTS).

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS users (
    kakao_id        BIGINT        NOT NULL,
    nickname        VARCHAR(255)  DEFAULT NULL,
    access_token    VARCHAR(512)  DEFAULT NULL,
    refresh_token   VARCHAR(512)  DEFAULT NULL,
    expires_at      VARCHAR(32)   DEFAULT NULL,
    enabled         TINYINT(1)    NOT NULL DEFAULT 1,
    created_at      VARCHAR(32)   DEFAULT NULL,
    custom_keywords TEXT          NOT NULL,
    custom_regions  TEXT          NOT NULL,
    PRIMARY KEY (kakao_id),
    KEY idx_users_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS jobs (
    id          VARCHAR(16)  NOT NULL,
    source      VARCHAR(64)  DEFAULT NULL,
    title       VARCHAR(512) DEFAULT NULL,
    org         VARCHAR(255) DEFAULT NULL,
    location    VARCHAR(255) DEFAULT NULL,
    job_type    VARCHAR(64)  DEFAULT NULL,
    deadline    VARCHAR(64)  DEFAULT NULL,
    url         VARCHAR(1024) DEFAULT NULL,
    crawled_at  VARCHAR(32)  DEFAULT NULL,
    is_new      TINYINT(1)   NOT NULL DEFAULT 1,
    notified    TINYINT(1)   NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY idx_jobs_is_new_crawled (is_new, crawled_at),
    KEY idx_jobs_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS job_reads (
    kakao_id BIGINT      NOT NULL,
    job_id   VARCHAR(16) NOT NULL,
    read_at  VARCHAR(32) NOT NULL,
    PRIMARY KEY (kakao_id, job_id),
    KEY idx_reads_user (kakao_id),
    KEY idx_reads_job  (job_id),
    CONSTRAINT fk_reads_user FOREIGN KEY (kakao_id) REFERENCES users(kakao_id) ON DELETE CASCADE,
    CONSTRAINT fk_reads_job  FOREIGN KEY (job_id)   REFERENCES jobs(id)       ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crawl_log (
    id         BIGINT      NOT NULL AUTO_INCREMENT,
    timestamp  VARCHAR(32) DEFAULT NULL,
    source     VARCHAR(64) DEFAULT NULL,
    found      INT         DEFAULT NULL,
    new_count  INT         DEFAULT NULL,
    status     VARCHAR(255) DEFAULT NULL,
    PRIMARY KEY (id),
    KEY idx_crawl_log_source (source),
    KEY idx_crawl_log_time (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
