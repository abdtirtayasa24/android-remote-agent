from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from timelapse.configuration import get_settings
from timelapse.database import close_database, session_scope
from timelapse.models.entities import Camera, CameraCredential
from timelapse.services.camera_credentials import (
    digest_camera_secret,
    generate_camera_credential,
)


class CommandError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage time-lapse camera credentials")

    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    register = subcommands.add_parser(
        "register-camera",
        help="Create a camera and issue its first credential",
    )
    register.add_argument("--slug", required=True)
    register.add_argument("--display-name", required=True)
    register.add_argument(
        "--valid-hours",
        type=int,
    )

    issue = subcommands.add_parser(
        "issue",
        help="Issue another credential for an existing camera",
    )
    issue.add_argument("--camera", required=True)
    issue.add_argument(
        "--valid-hours",
        type=int,
    )

    revoke = subcommands.add_parser(
        "revoke",
        help="Revoke a credential by token ID",
    )
    revoke.add_argument("--token-id", required=True)

    list_credentials = subcommands.add_parser(
        "list",
        help="List credential metadata without secrets",
    )
    list_credentials.add_argument("--camera", required=True)

    return parser


def calculate_expiration(
    valid_hours: int | None,
) -> datetime | None:
    if valid_hours is None:
        return None

    if valid_hours <= 0:
        raise CommandError("--valid-hours must be greater than zero")

    return datetime.now(UTC) + timedelta(hours=valid_hours)


async def register_camera(
    *,
    slug: str,
    display_name: str,
    valid_hours: int | None,
) -> None:
    settings = get_settings()
    generated = generate_camera_credential()
    expires_at = calculate_expiration(valid_hours)

    async with session_scope() as session:
        existing_camera = await session.scalar(select(Camera).where(Camera.slug == slug))

        if existing_camera is not None:
            raise CommandError(f"camera already exists: {slug}")

        camera = Camera(
            slug=slug,
            display_name=display_name,
        )
        session.add(camera)
        await session.flush()

        session.add(
            CameraCredential(
                camera_id=camera.id,
                token_id=generated.token_id,
                secret_digest=digest_camera_secret(
                    secret=generated.secret,
                    pepper=(settings.camera_token_pepper.get_secret_value()),
                ),
                expires_at=expires_at,
            )
        )

    print(f"Camera registered: {slug}")
    print(f"Token ID: {generated.token_id}")
    print()
    print("Camera credential:")
    print(generated.plaintext)
    print()
    print("Store this credential now. The plaintext secret cannot be recovered.")


async def issue_credential(
    *,
    camera_slug: str,
    valid_hours: int | None,
) -> None:
    settings = get_settings()
    generated = generate_camera_credential()
    expires_at = calculate_expiration(valid_hours)

    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_slug))

        if camera is None:
            raise CommandError(f"camera does not exist: {camera_slug}")

        if not camera.enabled:
            raise CommandError(f"camera is disabled: {camera_slug}")

        session.add(
            CameraCredential(
                camera_id=camera.id,
                token_id=generated.token_id,
                secret_digest=digest_camera_secret(
                    secret=generated.secret,
                    pepper=(settings.camera_token_pepper.get_secret_value()),
                ),
                expires_at=expires_at,
            )
        )

    print(f"Credential issued for: {camera_slug}")
    print(f"Token ID: {generated.token_id}")
    print()
    print("Camera credential:")
    print(generated.plaintext)
    print()
    print("Store this credential now. The plaintext secret cannot be recovered.")


async def revoke_credential(
    *,
    token_id: str,
) -> None:
    async with session_scope() as session:
        credential = await session.scalar(
            select(CameraCredential)
            .where(CameraCredential.token_id == token_id)
            .with_for_update()
        )

        if credential is None:
            raise CommandError(f"credential does not exist: {token_id}")

        if credential.revoked_at is not None:
            raise CommandError(f"credential is already revoked: {token_id}")

        credential.revoked_at = datetime.now(UTC)

    print(f"Credential revoked: {token_id}")


async def list_camera_credentials(
    *,
    camera_slug: str,
) -> None:
    async with session_scope() as session:
        camera = await session.scalar(select(Camera).where(Camera.slug == camera_slug))

        if camera is None:
            raise CommandError(f"camera does not exist: {camera_slug}")

        credentials = (
            await session.scalars(
                select(CameraCredential)
                .where(CameraCredential.camera_id == camera.id)
                .order_by(CameraCredential.created_at.desc())
            )
        ).all()

    print(f"Camera: {camera_slug}")

    if not credentials:
        print("No credentials.")
        return

    for credential in credentials:
        print(
            " ".join(
                (
                    f"token_id={credential.token_id}",
                    f"created_at={credential.created_at.isoformat()}",
                    f"expires_at={credential.expires_at}",
                    f"revoked_at={credential.revoked_at}",
                    f"last_used_at={credential.last_used_at}",
                )
            )
        )


async def run_command(
    arguments: argparse.Namespace,
) -> None:
    if arguments.command == "register-camera":
        await register_camera(
            slug=arguments.slug,
            display_name=arguments.display_name,
            valid_hours=arguments.valid_hours,
        )
        return

    if arguments.command == "issue":
        await issue_credential(
            camera_slug=arguments.camera,
            valid_hours=arguments.valid_hours,
        )
        return

    if arguments.command == "revoke":
        await revoke_credential(
            token_id=arguments.token_id,
        )
        return

    if arguments.command == "list":
        await list_camera_credentials(
            camera_slug=arguments.camera,
        )
        return

    raise CommandError(f"unsupported command: {arguments.command}")


async def async_main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()

    try:
        await run_command(arguments)
    except CommandError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        await close_database()

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
