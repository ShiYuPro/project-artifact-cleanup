#!/usr/bin/env python3
"""Run the real artifact CLI against generated, disposable sample files."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CLI = Path(__file__).resolve().with_name("artifacts.py")


def run(project, command, *args):
    result = subprocess.run(
        [sys.executable, str(CLI), command, "--project", str(project), *args],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    # TemporaryDirectory removes only the fixtures created by this demo.
    with tempfile.TemporaryDirectory(prefix="artifact-cleanup-demo-") as temporary:
        project = Path(temporary)
        source = project / "app.py"
        source.write_text("print('keep this source')\n", encoding="utf-8")
        source_before = source.read_bytes()
        identity = ("--task", "demo", "--owner", "demo")
        started = run(project, "begin", *identity, "--reason", "Generated demo fixtures")
        payload = Path(started["payload"])
        (payload / "scratch.txt").write_bytes(b"demo scratch\n")
        print("1. Created one 13-byte scratch file in a managed task; app.py stays outside.")

        preview = run(project, "finish", *identity, "--keep-days", "0")
        require(preview["would_delete_bytes"] == 13 and not preview["payload_deleted"]
                and (payload / "scratch.txt").exists(), "Preview did not preserve scratch")
        print("2. Zero-day preview: would_delete_bytes=13, payload_deleted=false.")

        completed = run(project, "finish", *identity, "--keep-days", "7")
        require(completed["state"] == "completed", "Retention failed")
        sweep = run(project, "sweep")
        require(sweep["deleted"] == [] and sweep["skipped"] ==
                [{"task": "demo", "reason": "not expired"}], "Retention was not respected")
        print("3. Retained for 7 days; sweep skips the task because it has not expired.")

        recovered = project / "recovered"
        restored = run(project, "restore", *identity, "--destination", str(recovered))
        require(restored["pinned"] and (recovered / "scratch.txt").read_bytes() == b"demo scratch\n",
                "Recovery failed")
        pinned_sweep = run(project, "sweep")
        require(pinned_sweep["deleted"] == [] and pinned_sweep["skipped"] ==
                [{"task": "demo", "reason": "pinned"}], "Pinned original was not protected")
        require(source.read_bytes() == source_before, "Source changed")
        print("4. Recovered identical bytes; original pinned and skipped by the next sweep.")
        print("5. app.py unchanged. PASS. Removing only this demo's temporary fixtures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
