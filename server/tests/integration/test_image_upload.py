from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import uuid4

import httpx
from PIL import Image
from sqlalchemy import func, select
from timelapse.database import get_session_factory
from timelapse.models.entities import Image as StoredImage


def create_jpeg(
    *,
    width: int = 1280,
    height: int = 720,
) -> bytes:
    output = BytesIO()

    Image.new(
        "RGB",
        (width, height),
        "white",
    ).save(
        output,
        format="JPEG",
        quality=72,
    )

    return output.getvalue()


async def upload(
    *,
    client: httpx.AsyncClient,
    camera_slug: str,
    credential: str,
    image_bytes: bytes,
    capture_id: str | None = None,
    sha256: str | None = None,
) -> httpx.Response:
    resolved_capture_id = capture_id or str(uuid4())
    resolved_sha256 = sha256 or hashlib.sha256(image_bytes).hexdigest()

    return await client.post(
        f"/api/v1/cameras/{camera_slug}/images",
        headers={
            "Authorization": f"Bearer {credential}",
        },
        data={
            "capture_id": resolved_capture_id,
            "captured_at_utc": "2026-07-14T01:00:00Z",
            "capture_source": "scheduled",
            "sha256": resolved_sha256,
        },
        files={
            "image": (
                "../../client-controlled-name.jpg",
                image_bytes,
                "image/jpeg",
            ),
        },
    )


async def test_valid_image_is_stored_once(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    camera = await create_camera(slug="front-door")
    image_bytes = create_jpeg()
    capture_id = str(uuid4())

    first_response = await upload(
        client=client,
        camera_slug=camera.slug,
        credential=camera.credential,
        image_bytes=image_bytes,
        capture_id=capture_id,
    )

    second_response = await upload(
        client=client,
        camera_slug=camera.slug,
        credential=camera.credential,
        image_bytes=image_bytes,
        capture_id=capture_id,
    )

    assert first_response.status_code == 201
    assert first_response.json()["status"] == "stored"

    assert second_response.status_code == 200
    assert second_response.json()["status"] == "already_stored"

    session_factory = get_session_factory()

    async with session_factory() as session:
        image_count = await session.scalar(select(func.count()).select_from(StoredImage))

        stored_image = await session.scalar(
            select(StoredImage).where(StoredImage.capture_id == capture_id)
        )

    assert image_count == 1
    assert stored_image is not None
    assert stored_image.storage_path.endswith(f"{capture_id}.jpg")
    assert "client-controlled-name" not in (stored_image.storage_path)


async def test_checksum_mismatch_is_rejected(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    camera = await create_camera(slug="front-door")

    response = await upload(
        client=client,
        camera_slug=camera.slug,
        credential=camera.credential,
        image_bytes=create_jpeg(),
        sha256="0" * 64,
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "checksum_mismatch"}}


async def test_invalid_credential_is_rejected(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    camera = await create_camera(slug="front-door")

    response = await upload(
        client=client,
        camera_slug=camera.slug,
        credential=("cam_AAAAAAAAAAAAAAAA_" + "A" * 43),
        image_bytes=create_jpeg(),
    )

    assert response.status_code == 401


async def test_revoked_credential_is_rejected(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    camera = await create_camera(
        slug="front-door",
        revoked=True,
    )

    response = await upload(
        client=client,
        camera_slug=camera.slug,
        credential=camera.credential,
        image_bytes=create_jpeg(),
    )

    assert response.status_code == 401


async def test_cross_camera_credential_is_rejected(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    first_camera = await create_camera(slug="front-door")
    await create_camera(slug="back-door")

    response = await upload(
        client=client,
        camera_slug="back-door",
        credential=first_camera.credential,
        image_bytes=create_jpeg(),
    )

    assert response.status_code == 401


async def test_oversized_image_is_rejected(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    camera = await create_camera(slug="front-door")

    oversized_bytes = b"x" * (5 * 1024 * 1024 + 1)

    response = await upload(
        client=client,
        camera_slug=camera.slug,
        credential=camera.credential,
        image_bytes=oversized_bytes,
    )

    assert response.status_code == 413
    assert response.json() == {"detail": {"code": "image_too_large"}}


async def test_invalid_jpeg_is_rejected(
    client: httpx.AsyncClient,
    create_camera,
) -> None:
    camera = await create_camera(slug="front-door")

    response = await upload(
        client=client,
        camera_slug=camera.slug,
        credential=camera.credential,
        image_bytes=b"this is not a jpeg",
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_jpeg"}}
