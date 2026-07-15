from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)

from timelapse.configuration import Settings, get_settings
from timelapse.models.enums import CaptureSource
from timelapse.schemas.images import ImageUploadResponse
from timelapse.services.camera_authentication import (
    AuthenticatedCamera,
    authenticate_camera,
)
from timelapse.services.image_files import (
    UploadRejectedError,
    receive_and_validate_upload,
)
from timelapse.services.image_upload import (
    ImageUploadCommand,
    parse_captured_at_utc,
    store_image_upload,
)

router = APIRouter(
    prefix="/api/v1/cameras",
    tags=["camera-images"],
)


@router.post(
    "/{camera_slug}/images",
    response_model=ImageUploadResponse,
    responses={
        200: {"description": "Capture was already stored"},
        201: {"description": "Capture was stored"},
        400: {"description": "HTTPS is required"},
        401: {"description": "Invalid camera credential"},
        409: {"description": "Capture identifier conflict"},
        413: {"description": "Image exceeds 5 MiB"},
        422: {"description": "Invalid upload"},
        500: {"description": "Image storage failed"},
    },
)
async def upload_image(
    response: Response,
    authenticated_camera: Annotated[
        AuthenticatedCamera,
        Depends(authenticate_camera),
    ],
    settings: Annotated[
        Settings,
        Depends(get_settings),
    ],
    capture_id: Annotated[
        UUID,
        Form(),
    ],
    captured_at_utc: Annotated[
        str,
        Form(min_length=20, max_length=40),
    ],
    capture_source: Annotated[
        CaptureSource,
        Form(),
    ],
    sha256: Annotated[
        str,
        Form(
            min_length=64,
            max_length=64,
            pattern=r"^[0-9a-fA-F]{64}$",
        ),
    ],
    image: Annotated[
        UploadFile,
        File(),
    ],
) -> ImageUploadResponse:
    validated_upload = None

    try:
        parsed_captured_at = parse_captured_at_utc(captured_at_utc)

        validated_upload = await receive_and_validate_upload(
            upload_file=image,
            expected_sha256=sha256,
            maximum_width=authenticated_camera.maximum_width,
            maximum_height=authenticated_camera.maximum_height,
            settings=settings,
        )

        result = await store_image_upload(
            authenticated_camera=authenticated_camera,
            command=ImageUploadCommand(
                capture_id=capture_id,
                captured_at_utc=parsed_captured_at,
                capture_source=capture_source,
                upload=validated_upload,
            ),
            settings=settings,
        )
    except UploadRejectedError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code},
        ) from exc
    finally:
        await image.close()

        if validated_upload is not None:
            validated_upload.temporary_path.unlink(missing_ok=True)

    response.status_code = 201 if result.status == "stored" else 200

    return ImageUploadResponse(
        capture_id=result.capture_id,
        status=result.status,
        received_at_utc=result.received_at_utc,
    )
