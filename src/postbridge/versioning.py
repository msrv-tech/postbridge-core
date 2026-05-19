from __future__ import annotations

import re

_VERSION_RE = re.compile(r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?(?P<suffix>.*)$")


def normalize_version_tag(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "v0.0.0"
    return raw if raw.startswith("v") else f"v{raw}"


def _version_parts(value: str | None) -> tuple[int, int, int, str]:
    raw = (value or "").strip()
    match = _VERSION_RE.match(raw)
    if not match:
        return (0, 0, 0, raw)
    suffix = match.group("suffix") or ""
    return (
        int(match.group("major") or 0),
        int(match.group("minor") or 0),
        int(match.group("patch") or 0),
        suffix,
    )


def is_newer_version(candidate: str | None, current: str | None) -> bool:
    candidate_parts = _version_parts(candidate)
    current_parts = _version_parts(current)
    return candidate_parts[:3] > current_parts[:3]


def build_release_update_command(*, image: str, version: str, compose_file: str = "deploy/docker-compose.release.yml") -> str:
    tag = normalize_version_tag(version)
    image_ref = f"{image}:{tag}"
    return (
        f"POSTBRIDGE_IMAGE={image_ref} docker compose -f {compose_file} --env-file .env pull\n"
        f"POSTBRIDGE_IMAGE={image_ref} docker compose -f {compose_file} --env-file .env up -d"
    )
