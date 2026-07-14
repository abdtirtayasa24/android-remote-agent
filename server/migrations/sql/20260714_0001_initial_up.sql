CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE camera_health_state AS ENUM (
    'online', 'degraded', 'offline', 'disabled'
);

CREATE TYPE capture_source AS ENUM (
    'scheduled', 'manual', 'motion'
);

CREATE TYPE image_storage_state AS ENUM (
    'staging', 'stored', 'missing', 'deleting'
);

CREATE TYPE job_status AS ENUM (
    'pending', 'processing', 'uploading',
    'completed', 'failed', 'cancelled'
);

CREATE TYPE analysis_status AS ENUM (
    'pending', 'processing', 'completed', 'skipped', 'failed'
);

CREATE TABLE cameras (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                      TEXT NOT NULL UNIQUE,
    display_name              TEXT NOT NULL,
    enabled                   BOOLEAN NOT NULL DEFAULT true,

    capture_interval_seconds  INTEGER NOT NULL DEFAULT 60
                              CHECK (capture_interval_seconds >= 30),
    retention_days            INTEGER NOT NULL DEFAULT 7
                              CHECK (retention_days BETWEEN 1 AND 365),
    jpeg_quality              SMALLINT NOT NULL DEFAULT 72
                              CHECK (jpeg_quality BETWEEN 1 AND 100),
    maximum_width             INTEGER NOT NULL DEFAULT 1280,
    maximum_height            INTEGER NOT NULL DEFAULT 720,

    motion_enabled            BOOLEAN NOT NULL DEFAULT true,
    motion_pixel_threshold    SMALLINT NOT NULL DEFAULT 25,
    motion_changed_ratio      NUMERIC(7,6) NOT NULL DEFAULT 0.020000,
    motion_region_ratio       NUMERIC(7,6) NOT NULL DEFAULT 0.005000,
    motion_cooldown_seconds   INTEGER NOT NULL DEFAULT 300,

    health_state              camera_health_state NOT NULL DEFAULT 'offline',
    last_seen_at              TIMESTAMPTZ,
    last_capture_at           TIMESTAMPTZ,
    last_upload_at            TIMESTAMPTZ,
    configuration_version     INTEGER NOT NULL DEFAULT 1,

    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE camera_credentials (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id           UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    token_id            VARCHAR(16) NOT NULL UNIQUE,
    secret_digest       BYTEA NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ,
    last_used_at        TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ
);

CREATE INDEX idx_camera_credentials_active
    ON camera_credentials (token_id)
    WHERE revoked_at IS NULL;

CREATE TABLE images (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    capture_id            UUID NOT NULL UNIQUE,
    camera_id             UUID NOT NULL REFERENCES cameras(id) ON DELETE RESTRICT,
    captured_at_utc       TIMESTAMPTZ NOT NULL,
    received_at_utc       TIMESTAMPTZ NOT NULL DEFAULT now(),
    capture_source        capture_source NOT NULL,
    storage_state         image_storage_state NOT NULL DEFAULT 'staging',
    storage_path          TEXT NOT NULL UNIQUE,
    mime_type             TEXT NOT NULL DEFAULT 'image/jpeg',
    file_size_bytes       BIGINT NOT NULL CHECK (file_size_bytes > 0),
    width_pixels          INTEGER NOT NULL CHECK (width_pixels > 0),
    height_pixels         INTEGER NOT NULL CHECK (height_pixels > 0),
    sha256                CHAR(64) NOT NULL,
    motion_detected       BOOLEAN,
    deleted_at            TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_images_camera_capture_time
    ON images (camera_id, captured_at_utc DESC)
    WHERE deleted_at IS NULL AND storage_state = 'stored';

CREATE INDEX idx_images_retention
    ON images (captured_at_utc)
    WHERE deleted_at IS NULL AND storage_state = 'stored';

CREATE TABLE motion_analyses (
    image_id                  UUID PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    previous_image_id         UUID REFERENCES images(id) ON DELETE SET NULL,
    status                    analysis_status NOT NULL DEFAULT 'pending',
    changed_pixel_ratio       NUMERIC(7,6),
    largest_region_ratio      NUMERIC(7,6),
    brightness_delta          NUMERIC(8,3),
    motion_detected           BOOLEAN,
    suppression_reason        TEXT,
    algorithm_version         TEXT NOT NULL,
    claimed_at                TIMESTAMPTZ,
    analyzed_at               TIMESTAMPTZ,
    error_message             TEXT
);

CREATE INDEX idx_motion_analyses_pending
    ON motion_analyses (status, image_id)
    WHERE status IN ('pending', 'processing');

CREATE TABLE motion_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id               UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    started_at_utc          TIMESTAMPTZ NOT NULL,
    last_detected_at_utc    TIMESTAMPTZ NOT NULL,
    ended_at_utc            TIMESTAMPTZ,
    peak_change_ratio       NUMERIC(7,6) NOT NULL,
    representative_image_id UUID NOT NULL REFERENCES images(id) ON DELETE RESTRICT,
    alert_status            TEXT NOT NULL DEFAULT 'pending'
                            CHECK (
                                alert_status IN (
                                    'pending', 'sent', 'failed', 'suppressed'
                                )
                            ),
    telegram_message_id     BIGINT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE motion_event_images (
    event_id       UUID NOT NULL REFERENCES motion_events(id) ON DELETE CASCADE,
    image_id       UUID NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    detected_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (event_id, image_id)
);

CREATE TABLE camera_heartbeats (
    id                       BIGSERIAL PRIMARY KEY,
    camera_id                UUID NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    received_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_sent_at           TIMESTAMPTZ,
    agent_version            TEXT NOT NULL,
    uptime_seconds           BIGINT,
    battery_percent          SMALLINT CHECK (battery_percent BETWEEN 0 AND 100),
    battery_status           TEXT,
    battery_temperature_c    NUMERIC(5,2),
    available_storage_bytes  BIGINT,
    pending_image_count      INTEGER,
    pending_image_bytes      BIGINT,
    oldest_pending_at        TIMESTAMPTZ,
    last_capture_at          TIMESTAMPTZ,
    last_upload_at           TIMESTAMPTZ,
    dropped_image_count      INTEGER NOT NULL DEFAULT 0,
    consecutive_capture_failures INTEGER NOT NULL DEFAULT 0,
    last_error_code          TEXT
);

CREATE INDEX idx_camera_heartbeats_camera_received
    ON camera_heartbeats (camera_id, received_at DESC);

CREATE TABLE telegram_principals (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id   BIGINT NOT NULL,
    telegram_chat_id   BIGINT NOT NULL,
    display_name       TEXT,
    role               TEXT NOT NULL DEFAULT 'viewer'
                       CHECK (role IN ('viewer', 'administrator')),
    enabled            BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (telegram_user_id, telegram_chat_id)
);

CREATE TABLE export_jobs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requested_by_user_id  BIGINT NOT NULL,
    destination_chat_id   BIGINT NOT NULL,
    camera_id             UUID NOT NULL REFERENCES cameras(id),
    start_at_utc          TIMESTAMPTZ NOT NULL,
    end_at_utc            TIMESTAMPTZ NOT NULL,
    status                job_status NOT NULL DEFAULT 'pending',
    matching_image_count  INTEGER,
    completed_part_count  INTEGER NOT NULL DEFAULT 0,
    claimed_at            TIMESTAMPTZ,
    error_code            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at            TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ,
    expires_at            TIMESTAMPTZ NOT NULL
                          DEFAULT (now() + INTERVAL '6 hours'),
    CHECK (end_at_utc > start_at_utc),
    CHECK (end_at_utc <= start_at_utc + INTERVAL '24 hours')
);

CREATE TABLE export_job_images (
    export_job_id  UUID NOT NULL
                   REFERENCES export_jobs(id) ON DELETE CASCADE,
    image_id       UUID NOT NULL REFERENCES images(id) ON DELETE RESTRICT,
    ordinal        INTEGER NOT NULL,
    PRIMARY KEY (export_job_id, image_id),
    UNIQUE (export_job_id, ordinal)
);

CREATE TABLE export_parts (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    export_job_id        UUID NOT NULL
                         REFERENCES export_jobs(id) ON DELETE CASCADE,
    part_number          INTEGER NOT NULL,
    storage_path         TEXT NOT NULL,
    file_size_bytes      BIGINT NOT NULL,
    sha256               CHAR(64) NOT NULL,
    status               TEXT NOT NULL DEFAULT 'created'
                         CHECK (
                             status IN (
                                 'created', 'uploading', 'sent',
                                 'failed', 'deleted'
                             )
                         ),
    telegram_message_id  BIGINT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at              TIMESTAMPTZ,
    UNIQUE (export_job_id, part_number)
);

CREATE TABLE audit_events (
    id                BIGSERIAL PRIMARY KEY,
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type        TEXT NOT NULL,
    camera_id         UUID REFERENCES cameras(id) ON DELETE SET NULL,
    telegram_user_id  BIGINT,
    telegram_chat_id  BIGINT,
    remote_ip         INET,
    outcome           TEXT NOT NULL,
    details           JSONB NOT NULL DEFAULT '{}'::jsonb
);
