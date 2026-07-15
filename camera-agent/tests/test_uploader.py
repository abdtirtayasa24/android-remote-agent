from camera_agent.uploader import (
    retry_delay_seconds,
)


def test_retry_backoff_without_jitter() -> None:
    assert (
        retry_delay_seconds(
            0,
            jitter_factor=1,
        )
        == 30
    )
    assert (
        retry_delay_seconds(
            1,
            jitter_factor=1,
        )
        == 60
    )
    assert (
        retry_delay_seconds(
            2,
            jitter_factor=1,
        )
        == 120
    )
    assert (
        retry_delay_seconds(
            3,
            jitter_factor=1,
        )
        == 300
    )
    assert (
        retry_delay_seconds(
            4,
            jitter_factor=1,
        )
        == 900
    )
    assert (
        retry_delay_seconds(
            5,
            jitter_factor=1,
        )
        == 1800
    )
    assert (
        retry_delay_seconds(
            6,
            jitter_factor=1,
        )
        == 3600
    )


def test_retry_backoff_is_capped() -> None:
    assert (
        retry_delay_seconds(
            100,
            jitter_factor=1,
        )
        == 3600
    )
