#!/usr/bin/env python3
"""Reuse successful CI evidence for an exact source revision, never for a branch name."""

import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request
import zipfile


def api(path, binary=False):
    if binary:
        # gh follows artifact redirects without forwarding GitHub credentials to
        # the signed storage host. urllib retains custom Authorization headers.
        result = subprocess.run(["gh", "api", path], capture_output=True)
        if result.returncode:
            raise RuntimeError("CI artifact download failed")
        return result.stdout
    request = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read() if binary else json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 4:
                raise RuntimeError(f"CI evidence request failed (HTTP {error.code})") from None
        except urllib.error.URLError:
            if attempt == 4:
                raise RuntimeError("CI evidence request failed after retries") from None
        time.sleep(min(2 ** attempt, 15))


def unpack(repo, artifact, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(api(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip", True))) as archive:
        for name in archive.namelist():
            if name in {"coverage-core.xml", "coverage-saas.xml", "proof.json"}:
                (output / name).write_bytes(archive.read(name))
    proof = json.loads((output / "proof.json").read_text())
    return proof


def source(repo, sha, output, timeout=1800):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("CI evidence requires a full source SHA")
    deadline = time.monotonic() + timeout
    while True:
        runs = api(f"repos/{repo}/actions/workflows/ci.yml/runs?event=push&branch=main&head_sha={sha}&per_page=5")["workflow_runs"]
        if runs:
            run = max(runs, key=lambda item: item["id"])
            jobs = api(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
            checks = {job["name"]: job.get("conclusion") for job in jobs}
            required = {"test", "secret-scan"} if repo.endswith("postbridge-core") else {"test"}
            if all(checks.get(name) == "success" for name in required):
                name = "coverage-core" if repo.endswith("postbridge-core") else "coverage-saas"
                artifacts = api(f"repos/{repo}/actions/runs/{run['id']}/artifacts")["artifacts"]
                artifact = next((a for a in artifacts if a["name"] == name and not a["expired"]), None)
                if artifact:
                    proof = unpack(repo, artifact, output)
                    if proof.get("sha") != sha:
                        raise RuntimeError("CI evidence does not match the requested source")
                    return proof
                raise RuntimeError("Successful CI has no reusable evidence; rerun CI")
            if any(checks.get(name) in {"failure", "cancelled", "timed_out"} for name in required):
                raise RuntimeError("Required source checks failed")
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for exact source CI evidence")
        time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    source(args.repo, args.sha, args.output, args.timeout)
    print("Verified CI evidence for " + args.sha)


if __name__ == "__main__":
    main()
