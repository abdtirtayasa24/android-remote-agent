from timelapse.models import Base

EXPECTED_TABLES = {
    "audit_events",
    "camera_credentials",
    "camera_heartbeats",
    "cameras",
    "export_job_images",
    "export_jobs",
    "export_parts",
    "images",
    "motion_analyses",
    "motion_event_images",
    "motion_events",
    "telegram_principals",
}


def test_all_baseline_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_required_partial_indexes_are_registered() -> None:
    index_names = {
        index.name for table in Base.metadata.tables.values() for index in table.indexes
    }

    assert {
        "idx_camera_credentials_active",
        "idx_images_camera_capture_time",
        "idx_images_retention",
        "idx_motion_analyses_pending",
        "idx_camera_heartbeats_camera_received",
    }.issubset(index_names)
