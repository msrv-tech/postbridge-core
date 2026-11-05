#!/usr/bin/env python3
"""Start the private RU release immediately after Core checks and await its result."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

WORKFLOW = "repos/msrv-tech/postbridge-saas/actions/workflows/deploy-ru.yml"


def api(path, method="GET", body=None):
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("The RU dispatch credential is not configured")
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2026-03-10"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub RU workflow request failed (HTTP {error.code})") from None


def trigger_and_wait(sha, fetch=api, pause=time.sleep, attempts=480):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("A full Core commit SHA is required")
    dispatched = fetch(WORKFLOW + "/dispatches", "POST", {"ref": "main", "inputs": {"core_sha": sha}})
    run_id = dispatched.get("workflow_run_id")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        raise RuntimeError("GitHub did not return the dispatched RU workflow run ID")
    print(f"RU release: https://github.com/msrv-tech/postbridge-saas/actions/runs/{run_id}", flush=True)
    last = None
    for attempt in range(attempts):
        run = fetch(f"repos/msrv-tech/postbridge-saas/actions/runs/{run_id}")
        state = (run["status"], run.get("conclusion"))
        if state != last:
            print(f"RU workflow: {state[0]} / {state[1] or 'pending'}", flush=True)
            last = state
        if run["status"] == "completed":
            if run.get("conclusion") != "success":
                raise RuntimeError("The private RU deployment workflow did not succeed")
            return run_id
        if attempt + 1 < attempts:
            pause(10)
    raise RuntimeError("Timed out awaiting the private RU deployment workflow")


if __name__ == "__main__":
    try:
        if "--check-access" in sys.argv:
            workflow = api(WORKFLOW)
            if workflow.get("state") != "active":
                raise RuntimeError("The private RU deployment workflow is not active")
            print("Private RU workflow access verified")
        else:
            trigger_and_wait(os.environ.get("GITHUB_SHA", ""))
    except (RuntimeError, ValueError, urllib.error.URLError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
