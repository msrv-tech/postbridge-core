from postbridge.versioning import build_release_update_command, is_newer_version, normalize_version_tag


def test_normalize_version_tag_adds_v_prefix():
    assert normalize_version_tag("0.1.2") == "v0.1.2"
    assert normalize_version_tag("v0.1.2") == "v0.1.2"
    assert normalize_version_tag("") == "v0.0.0"
    assert normalize_version_tag(None) == "v0.0.0"


def test_is_newer_version_compares_semver_numbers():
    assert is_newer_version("v0.1.10", "v0.1.2")
    assert is_newer_version("v0.2.0", "v0.1.99")
    assert not is_newer_version("v0.1.2", "v0.1.2")
    assert not is_newer_version("v0.1.1", "v0.1.2")


def test_build_release_update_command_pins_requested_tag():
    command = build_release_update_command(image="ghcr.io/example/app", version="v1.2.3")
    assert "POSTBRIDGE_IMAGE=ghcr.io/example/app:v1.2.3" in command
    assert "docker compose -f deploy/docker-compose.release.yml --env-file .env pull" in command
    assert "docker compose -f deploy/docker-compose.release.yml --env-file .env up -d" in command
