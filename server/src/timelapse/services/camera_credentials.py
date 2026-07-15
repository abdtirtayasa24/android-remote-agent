from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string
from dataclasses import dataclass

TOKEN_ID_LENGTH = 16
SECRET_BYTE_LENGTH = 32

_TOKEN_ID_ALPHABET = string.ascii_letters + string.digits

_CREDENTIAL_PATTERN = re.compile(
    r"^cam_"
    r"(?P<token_id>[A-Za-z0-9]{16})"
    r"_"
    r"(?P<secret>[A-Za-z0-9_-]{43})"
    r"$"
)


@dataclass(frozen=True)
class GeneratedCameraCredential:
    token_id: str
    secret: str
    plaintext: str


@dataclass(frozen=True)
class ParsedCameraCredential:
    token_id: str
    secret: str


def generate_camera_credential() -> GeneratedCameraCredential:
    token_id = "".join(secrets.choice(_TOKEN_ID_ALPHABET) for _ in range(TOKEN_ID_LENGTH))
    secret = secrets.token_urlsafe(SECRET_BYTE_LENGTH)

    return GeneratedCameraCredential(
        token_id=token_id,
        secret=secret,
        plaintext=f"cam_{token_id}_{secret}",
    )


def parse_camera_credential(
    plaintext: str,
) -> ParsedCameraCredential | None:
    match = _CREDENTIAL_PATTERN.fullmatch(plaintext)

    if match is None:
        return None

    return ParsedCameraCredential(
        token_id=match.group("token_id"),
        secret=match.group("secret"),
    )


def digest_camera_secret(
    *,
    secret: str,
    pepper: str,
) -> bytes:
    return hmac.new(
        key=pepper.encode("utf-8"),
        msg=secret.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()


def camera_secret_matches(
    *,
    secret: str,
    expected_digest: bytes,
    pepper: str,
) -> bool:
    supplied_digest = digest_camera_secret(
        secret=secret,
        pepper=pepper,
    )

    return hmac.compare_digest(
        supplied_digest,
        expected_digest,
    )
