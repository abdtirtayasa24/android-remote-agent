from timelapse.services.camera_credentials import (
    camera_secret_matches,
    digest_camera_secret,
    generate_camera_credential,
    parse_camera_credential,
)


def test_generated_credential_can_be_parsed() -> None:
    generated = generate_camera_credential()

    parsed = parse_camera_credential(generated.plaintext)

    assert parsed is not None
    assert parsed.token_id == generated.token_id
    assert parsed.secret == generated.secret


def test_invalid_credential_format_is_rejected() -> None:
    assert parse_camera_credential("not-a-camera-token") is None
    assert parse_camera_credential("cam_short_secret") is None


def test_camera_secret_digest_matches_correct_secret() -> None:
    pepper = "test-pepper-" * 4
    secret = generate_camera_credential().secret

    digest = digest_camera_secret(
        secret=secret,
        pepper=pepper,
    )

    assert camera_secret_matches(
        secret=secret,
        expected_digest=digest,
        pepper=pepper,
    )


def test_camera_secret_digest_rejects_wrong_secret() -> None:
    pepper = "test-pepper-" * 4
    generated = generate_camera_credential()

    digest = digest_camera_secret(
        secret=generated.secret,
        pepper=pepper,
    )

    assert not camera_secret_matches(
        secret="incorrect-secret",  # noqa: S106
        expected_digest=digest,
        pepper=pepper,
    )
