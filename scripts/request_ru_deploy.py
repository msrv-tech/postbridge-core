#!/usr/bin/env python3
"""Start one shared private release after Core CI and track its CD result."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

WORKFLOW = "repos/msrv-tech/postbridge-saas/actions/workflows/deploy.yml"


def workflow(region):
    if region not in {"both", "ru", "io"}:
        raise ValueError("Unknown production region")
    return WORKFLOW


def api(path, method="GET", body=None):
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("The production dispatch credential is not configured")
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2026-03-10"},
    )
    # A dispatch POST must not be repeated after an ambiguous network failure.
    # Status reads are idempotent and tolerate transient GitHub/network failures.
    for attempt in range(5 if method == "GET" else 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or method != "GET" or attempt == 4:
                raise RuntimeError(f"GitHub deployment request failed (HTTP {error.code})") from None
        except urllib.error.URLError:
            if method != "GET" or attempt == 4:
                raise RuntimeError("GitHub deployment request failed; inspect the private release history") from None
        time.sleep(min(2 ** attempt, 15))


def trigger_and_wait(sha, fetch=api, pause=time.sleep, attempts=1350, region="both"):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("A full Core commit SHA is required")
    dispatched = fetch(workflow(region) + "/dispatches", "POST", {"ref": "main", "inputs": {"core_sha": sha, "target": region}})
    run_id = dispatched.get("workflow_run_id")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        raise RuntimeError("GitHub did not return the dispatched production workflow run ID")
    print(f"{region.upper()} release: https://github.com/msrv-tech/postbridge-saas/actions/runs/{run_id}", flush=True)
    last = None
    for attempt in range(attempts):
        run = fetch(f"repos/msrv-tech/postbridge-saas/actions/runs/{run_id}")
        state = (run["status"], run.get("conclusion"))
        if state != last:
            print(f"{region.upper()} workflow: {state[0]} / {state[1] or 'pending'}", flush=True)
            last = state
        if run["status"] == "completed":
            if run.get("conclusion") != "success":
                raise RuntimeError("The private deployment workflow did not succeed")
            return run_id
        if attempt + 1 < attempts:
            pause(10)
    raise RuntimeError("Timed out awaiting deployment; the private release may still be active")


if __name__ == "__main__":
    try:
        if "--check-access" in sys.argv:
            for region in ("both",):
                if api(workflow(region)).get("state") != "active":
                    raise RuntimeError("A private deployment workflow is not active")
            print("Private production workflow access verified")
        else:
            trigger_and_wait(os.environ.get("CORE_SHA", os.environ.get("GITHUB_SHA", "")),
                             region=os.environ.get("DEPLOY_REGION", "both"))
    except (RuntimeError, ValueError, urllib.error.URLError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
