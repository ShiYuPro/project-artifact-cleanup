#!/usr/bin/env python3
"""Small, local-only lifecycle manager for agent-created artifact payloads."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

TASK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ArtifactError(RuntimeError):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ArtifactError("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ArtifactError("invalid timestamp") from exc


def regular(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ArtifactError(f"missing path: {path}") from exc
    if not path.is_file() or path.is_symlink() or info.st_nlink != 1:
        raise ArtifactError(f"unsafe non-regular file: {path}")
    return info


def directory(path: Path, *, missing_ok: bool = False) -> bool:
    if not path.exists() and not path.is_symlink():
        if missing_ok:
            return False
        raise ArtifactError(f"missing directory: {path}")
    if path.is_symlink() or not path.is_dir():
        raise ArtifactError(f"unsafe directory: {path}")
    return True


def task_name(value: str) -> str:
    if not TASK_RE.fullmatch(value) or value in {".", ".."}:
        raise ArtifactError("invalid task; use a simple name without path separators")
    return value


def project_path(value: str) -> Path:
    path = Path(value).resolve(strict=True)
    directory(path)
    return path


def store_for(project: Path) -> Path:
    return project / ".agent-artifacts"


def existing_store(project: Path) -> Path | None:
    store = store_for(project)
    if not store.exists() and not store.is_symlink():
        return None
    directory(store)
    for part in ("tmp", "recent"):
        directory(store / part)
    return store


def create_store(project: Path) -> Path:
    store = store_for(project)
    if store.exists() or store.is_symlink():
        directory(store)
    else:
        try:
            store.mkdir(mode=0o700)
        except FileExistsError:
            directory(store)
    for part in ("tmp", "recent"):
        child = store / part
        if child.exists() or child.is_symlink():
            directory(child)
        else:
            try:
                child.mkdir(mode=0o700)
            except FileExistsError:
                directory(child)
    return store


@contextmanager
def lock(store: Path):
    directory(store)
    lockfile = store / ".lock"
    if lockfile.exists() or lockfile.is_symlink():
        regular(lockfile)
    else:
        try:
            fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
        except FileExistsError:
            regular(lockfile)
    with lockfile.open("r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json(path: Path, data: dict) -> None:
    directory(path.parent)
    temporary = path.parent / ("." + path.name + ".new")
    if temporary.exists() or temporary.is_symlink():
        raise ArtifactError(f"unexpected temporary path: {temporary}")
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def payload_fingerprint(payload: Path) -> tuple[str, int, float]:
    directory(payload)
    digest = hashlib.sha256()
    total = 0
    latest = payload.lstat().st_mtime

    def visit(current: Path, relative: Path) -> None:
        nonlocal total, latest
        for child in sorted(os.scandir(current), key=lambda entry: entry.name):
            path = Path(child.path)
            rel = relative / child.name
            info = path.lstat()
            if path.is_symlink():
                raise ArtifactError(f"payload contains symlink: {rel}")
            if child.is_dir(follow_symlinks=False):
                digest.update(b"D\0" + str(rel).encode() + b"\0")
                latest = max(latest, info.st_mtime)
                visit(path, rel)
            elif child.is_file(follow_symlinks=False) and info.st_nlink == 1:
                digest.update(b"F\0" + str(rel).encode() + b"\0" + str(info.st_size).encode() + b"\0")
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                total += info.st_size
                latest = max(latest, info.st_mtime)
            else:
                raise ArtifactError(f"payload contains unsafe entry: {rel}")

    visit(payload, Path("."))
    return digest.hexdigest(), total, latest


def load_metadata(group: Path, project: Path, task: str | None = None) -> dict:
    directory(group)
    metadata_path = group / "metadata.json"
    regular(metadata_path)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid metadata: {group.name}") from exc
    if not isinstance(metadata, dict):
        raise ArtifactError("invalid metadata object")
    expected_task = task or group.name
    if (metadata.get("project") != str(project) or metadata.get("task") != expected_task
            or not isinstance(metadata.get("owner"), str) or not metadata["owner"]):
        raise ArtifactError("metadata binding mismatch")
    return metadata


def validate_group(group: Path) -> tuple[str, int, float]:
    """Allow deletion only for the exact two-entry group shape we create."""
    directory(group)
    entries = {entry.name for entry in os.scandir(group)}
    if entries != {"metadata.json", "payload"}:
        raise ArtifactError("group contains unknown or missing entries")
    regular(group / "metadata.json")
    return payload_fingerprint(group / "payload")


def group_path(store: Path, bucket: str, task: str) -> Path:
    parent = store / bucket
    directory(parent)
    path = parent / task
    if path.parent != parent:
        raise ArtifactError("path escaped artifact store")
    return path


def begin(args: argparse.Namespace) -> dict:
    project = project_path(args.project)
    task = task_name(args.task)
    if not args.owner or not args.reason:
        raise ArtifactError("owner and reason are required")
    store = create_store(project)
    with lock(store):
        tmp = group_path(store, "tmp", task)
        recent = group_path(store, "recent", task)
        if tmp.exists() or tmp.is_symlink() or recent.exists() or recent.is_symlink():
            raise ArtifactError("task already exists; tasks are never reused")
        tmp.mkdir(mode=0o700)
        (tmp / "payload").mkdir(mode=0o700)
        atomic_json(tmp / "metadata.json", {
            "project": str(project), "task": task, "owner": args.owner, "reason": args.reason,
            "state": "active", "created_at": stamp(now()),
        })
    return {"task": task, "payload": str(tmp / "payload"), "state": "active"}


def finish(args: argparse.Namespace) -> dict:
    project = project_path(args.project)
    task = task_name(args.task)
    store = existing_store(project)
    if store is None:
        raise ArtifactError("artifact store does not exist")
    with lock(store):
        tmp = group_path(store, "tmp", task)
        recent = group_path(store, "recent", task)
        if not tmp.exists() or tmp.is_symlink() or recent.exists() or recent.is_symlink():
            raise ArtifactError("active task not found")
        metadata = load_metadata(tmp, project, task)
        if metadata.get("owner") != args.owner or metadata.get("state") != "active":
            raise ArtifactError("owner or active-state mismatch")
        fingerprint, total, latest = validate_group(tmp)
        closed = now()
        if args.keep_days == 0:
            if metadata.get("pinned"):
                raise ArtifactError("pinned active task cannot be discarded")
            if not args.apply:
                return {"task": task, "state": "active", "would_delete_bytes": total, "payload_deleted": False}
            remove_group(store, tmp, "tmp", fingerprint, total)
            return {"task": task, "state": "discarded", "payload_deleted": True}
        expires = max(closed.timestamp(), latest) + timedelta(days=args.keep_days).total_seconds()
        metadata.update({"state": "completed", "closed_at": stamp(closed),
                         "expires_at": stamp(datetime.fromtimestamp(expires, timezone.utc)),
                         "fingerprint": fingerprint, "total_bytes": total,
                         "pinned": bool(metadata.get("pinned", False))})
        atomic_json(tmp / "metadata.json", metadata)
        os.replace(tmp, recent)
        return {"task": task, "state": "completed", "expires_at": metadata["expires_at"],
                "total_bytes": total, "payload": str(recent / "payload")}


def pin(args: argparse.Namespace) -> dict:
    project = project_path(args.project)
    task = task_name(args.task)
    store = existing_store(project)
    if store is None:
        raise ArtifactError("artifact store does not exist")
    with lock(store):
        tmp = group_path(store, "tmp", task)
        recent = group_path(store, "recent", task)
        group = tmp if tmp.exists() or tmp.is_symlink() else recent
        metadata = load_metadata(group, project, task)
        if metadata.get("owner") != args.owner or metadata.get("state") not in {"active", "completed"}:
            raise ArtifactError("owner or pinnable-state mismatch")
        validate_group(group)
        metadata.update({"pinned": True, "pinned_at": stamp(now())})
        atomic_json(group / "metadata.json", metadata)
    return {"task": task, "pinned": True}


def restore(args: argparse.Namespace) -> dict:
    project = project_path(args.project)
    task = task_name(args.task)
    store = existing_store(project)
    if store is None:
        raise ArtifactError("artifact store does not exist")
    destination = Path(args.destination).absolute()
    # Keep recovery outside the managed store so the next sweep cannot remove it.
    resolved = destination.resolve()
    if resolved == store or store in resolved.parents:
        raise ArtifactError("restore destination must be outside artifact store")
    if destination.exists() or destination.is_symlink():
        raise ArtifactError("restore destination must not exist")
    directory(destination.parent)
    with lock(store):
        group = group_path(store, "recent", task)
        metadata = load_metadata(group, project, task)
        if metadata.get("owner") != args.owner or metadata.get("state") != "completed":
            raise ArtifactError("owner or completed-state mismatch")
        fingerprint, total, _ = validate_group(group)
        # Pin before copying: partial recovery must never expire its only source.
        metadata.update({"pinned": True, "pinned_at": stamp(now())})
        atomic_json(group / "metadata.json", metadata)
        shutil.copytree(group / "payload", destination)
        copied, copied_total, _ = payload_fingerprint(destination)
        if (copied, copied_total) != (fingerprint, total):
            raise ArtifactError("restore verification failed; original pinned, inspect destination")
    return {"task": task, "destination": str(destination), "total_bytes": total, "pinned": True}


def inspect_bucket(store: Path, project: Path, bucket: str) -> list[dict]:
    parent = store / bucket
    directory(parent)
    rows: list[dict] = []
    for entry in sorted(os.scandir(parent), key=lambda item: item.name):
        path = Path(entry.path)
        if path.is_symlink() or not entry.is_dir(follow_symlinks=False):
            rows.append({"task": entry.name, "bucket": bucket, "state": "invalid", "reason": "unsafe group"})
            continue
        try:
            metadata = load_metadata(path, project, entry.name)
            if metadata.get("state") in {"active", "completed"}:
                validate_group(path)
            else:
                raise ArtifactError("unknown artifact state")
            rows.append({"task": entry.name, "bucket": bucket, "state": metadata.get("state"), "owner": metadata["owner"],
                         "pinned": bool(metadata.get("pinned", False)), "expires_at": metadata.get("expires_at")})
        except ArtifactError as exc:
            rows.append({"task": entry.name, "bucket": bucket, "state": "invalid", "reason": str(exc)})
    return rows


def status(args: argparse.Namespace) -> dict:
    project = project_path(args.project)
    store = existing_store(project)
    if store is None:
        return {"artifacts": []}
    return {"artifacts": inspect_bucket(store, project, "tmp") + inspect_bucket(store, project, "recent")}


def remove_group(store: Path, group: Path, bucket: str, expected_fingerprint: str, expected_total: int) -> None:
    parent = store / bucket
    if bucket not in {"tmp", "recent"} or group.parent != parent or group.is_symlink() or not group.is_dir():
        raise ArtifactError("refusing deletion outside artifact store")
    fingerprint, total, _ = validate_group(group)
    if fingerprint != expected_fingerprint or total != expected_total:
        raise ArtifactError("payload changed before deletion")
    shutil.rmtree(group)


def sweep(args: argparse.Namespace) -> dict:
    project = project_path(args.project)
    store = existing_store(project)
    if store is None:
        return {"candidates": [], "deleted": [], "skipped": [], "candidate_bytes": 0, "deleted_bytes": 0}
    candidate_bytes = 0
    deleted_bytes = 0
    candidates: list[str] = []
    deleted: list[str] = []
    skipped: list[dict] = []
    with lock(store):
        recent = store / "recent"
        for entry in sorted(os.scandir(recent), key=lambda item: item.name):
            group = Path(entry.path)
            if group.is_symlink() or not entry.is_dir(follow_symlinks=False):
                skipped.append({"task": entry.name, "reason": "unsafe group"})
                continue
            try:
                metadata = load_metadata(group, project, entry.name)
                if metadata.get("state") != "completed":
                    skipped.append({"task": entry.name, "reason": "not completed"})
                    continue
                if metadata.get("pinned"):
                    skipped.append({"task": entry.name, "reason": "pinned"})
                    continue
                if now() < parse_stamp(metadata.get("expires_at")):
                    skipped.append({"task": entry.name, "reason": "not expired"})
                    continue
                fingerprint, total, _ = validate_group(group)
                if fingerprint != metadata.get("fingerprint") or total != metadata.get("total_bytes"):
                    skipped.append({"task": entry.name, "reason": "payload changed"})
                    continue
                candidates.append(entry.name)
                candidate_bytes += total
                if args.apply:
                    remove_group(store, group, "recent", metadata["fingerprint"], metadata["total_bytes"])
                    deleted.append(entry.name)
                    deleted_bytes += total
            except ArtifactError as exc:
                skipped.append({"task": entry.name, "reason": str(exc)})
    return {"candidates": candidates, "deleted": deleted, "skipped": skipped,
            "candidate_bytes": candidate_bytes, "deleted_bytes": deleted_bytes}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("begin", "finish", "pin"):
        sub = commands.add_parser(name)
        sub.add_argument("--project", required=True)
        sub.add_argument("--task", required=True)
        sub.add_argument("--owner", required=True)
    begin_parser = commands.choices["begin"]
    begin_parser.add_argument("--reason", required=True)
    finish_parser = commands.choices["finish"]
    finish_parser.add_argument("--keep-days", type=int, choices=range(0, 3651), metavar="0..3650", default=7)
    finish_parser.add_argument("--apply", action="store_true", help="Delete zero-day scratch; otherwise preview")
    restore_parser = commands.add_parser("restore")
    for option in ("project", "task", "owner", "destination"):
        restore_parser.add_argument("--" + option, required=True)
    for name in ("sweep", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--project", required=True)
    commands.choices["sweep"].add_argument("--apply", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = {"begin": begin, "finish": finish, "pin": pin, "sweep": sweep, "status": status, "restore": restore}[args.command](args)
    except (ArtifactError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
