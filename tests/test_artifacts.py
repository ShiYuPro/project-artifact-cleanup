import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

MODULE = Path(__file__).parents[1] / "scripts" / "artifacts.py"
SPEC = importlib.util.spec_from_file_location("artifacts", MODULE)
artifacts = importlib.util.module_from_spec(SPEC)
sys.modules["artifacts"] = artifacts
SPEC.loader.exec_module(artifacts)


class ArtifactCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args):
        return artifacts.main(list(args))

    def begin(self, task="one", owner="alice"):
        self.assertEqual(0, self.call("begin", "--project", str(self.project), "--task", task,
                                     "--owner", owner, "--reason", "test"))
        return self.project / ".agent-artifacts" / "tmp" / task / "payload"

    def finish(self, task="one", owner="alice", days=7, apply=False):
        return self.call("finish", "--project", str(self.project), "--task", task,
                         "--owner", owner, "--keep-days", str(days), *(["--apply"] if apply else []))

    def metadata(self, task="one"):
        return json.loads((self.project / ".agent-artifacts" / "recent" / task / "metadata.json").read_text())

    def expire(self, task="one"):
        path = self.project / ".agent-artifacts" / "recent" / task / "metadata.json"
        data = json.loads(path.read_text())
        data["expires_at"] = "2000-01-01T00:00:00Z"
        path.write_text(json.dumps(data))

    def test_no_store_status_and_sweep_are_read_only(self):
        self.assertEqual(0, self.call("status", "--project", str(self.project)))
        self.assertEqual(0, self.call("sweep", "--project", str(self.project)))
        self.assertFalse((self.project / ".agent-artifacts").exists())

    def test_begin_creates_active_payload_and_rejects_reuse(self):
        payload = self.begin()
        self.assertTrue(payload.is_dir())
        self.assertEqual(0, self.call("status", "--project", str(self.project)))
        self.assertEqual(2, self.call("begin", "--project", str(self.project), "--task", "one",
                                      "--owner", "alice", "--reason", "again"))

    def test_finish_moves_to_recent_with_fingerprint_and_expiry(self):
        payload = self.begin()
        (payload / "proof.txt").write_text("safe")
        self.assertEqual(0, self.finish())
        data = self.metadata()
        self.assertEqual("completed", data["state"])
        self.assertEqual(4, data["total_bytes"])
        self.assertTrue(data["fingerprint"])
        self.assertFalse((self.project / ".agent-artifacts" / "tmp" / "one").exists())
        self.assertTrue((self.project / ".agent-artifacts" / "recent" / "one" / "payload" / "proof.txt").exists())

    def test_zero_day_finish_deletes_the_entire_group(self):
        self.begin()
        self.assertEqual(0, self.finish(days=0, apply=True))
        group = self.project / ".agent-artifacts" / "recent" / "one"
        self.assertFalse(group.exists())
        self.assertFalse((self.project / ".agent-artifacts" / "tmp" / "one").exists())
        self.assertEqual(0, self.call("begin", "--project", str(self.project), "--task", "one",
                                      "--owner", "alice", "--reason", "reused"))

    def test_expiry_is_not_earlier_than_latest_payload_mtime(self):
        payload = self.begin()
        file = payload / "future"
        file.write_text("x")
        future = time.time() + 3600
        os.utime(file, (future, future))
        self.assertEqual(0, self.finish())
        expires = artifacts.parse_stamp(self.metadata()["expires_at"]).timestamp()
        self.assertGreaterEqual(expires + 0.000001, future + 7 * 24 * 3600)

    def test_owner_binding_is_enforced(self):
        self.begin()
        self.assertEqual(2, self.finish(owner="mallory"))
        self.assertEqual(0, self.finish())
        self.assertEqual(2, self.call("pin", "--project", str(self.project), "--task", "one",
                                      "--owner", "mallory"))

    def test_active_pin_is_inherited_by_finish_and_prevents_zero_day_discard(self):
        self.begin("keep")
        self.assertEqual(0, self.call("pin", "--project", str(self.project), "--task", "keep", "--owner", "alice"))
        self.assertEqual(2, self.finish("keep", days=0))
        self.assertEqual(0, self.finish("keep"))
        self.assertTrue(self.metadata("keep")["pinned"])
        self.expire("keep")
        self.assertEqual(0, self.call("sweep", "--project", str(self.project), "--apply"))
        self.assertTrue((self.project / ".agent-artifacts" / "recent" / "keep").exists())

    def test_unexpired_and_active_tasks_are_not_swept(self):
        self.begin("active")
        self.begin("recent")
        self.assertEqual(0, self.finish("recent"))
        self.assertEqual(0, self.call("sweep", "--project", str(self.project), "--apply"))
        root = self.project / ".agent-artifacts"
        self.assertTrue((root / "tmp" / "active").exists())
        self.assertTrue((root / "recent" / "recent").exists())

    def test_expired_completed_task_is_deleted_only_with_apply(self):
        self.begin()
        self.assertEqual(0, self.finish())
        self.expire()
        self.assertEqual(0, self.call("sweep", "--project", str(self.project)))
        group = self.project / ".agent-artifacts" / "recent" / "one"
        self.assertTrue(group.exists())
        self.assertEqual(0, self.call("sweep", "--project", str(self.project), "--apply"))
        self.assertFalse(group.exists())

    def test_pinned_and_later_modified_payloads_are_skipped(self):
        self.begin("pinned")
        self.assertEqual(0, self.finish("pinned"))
        self.expire("pinned")
        self.assertEqual(0, self.call("pin", "--project", str(self.project), "--task", "pinned", "--owner", "alice"))
        self.begin("changed")
        changed = self.project / ".agent-artifacts" / "tmp" / "changed" / "payload"
        (changed / "original").write_text("a")
        self.assertEqual(0, self.finish("changed"))
        self.expire("changed")
        (self.project / ".agent-artifacts" / "recent" / "changed" / "payload" / "original").write_text("b")
        self.assertEqual(0, self.call("sweep", "--project", str(self.project), "--apply"))
        recent = self.project / ".agent-artifacts" / "recent"
        self.assertTrue((recent / "pinned").exists())
        self.assertTrue((recent / "changed").exists())

    def test_payload_symlink_and_hardlink_are_rejected(self):
        payload = self.begin("link")
        target = self.project / "outside"
        target.write_text("x")
        (payload / "bad").symlink_to(target)
        self.assertEqual(2, self.finish("link"))
        payload = self.begin("hard")
        source = payload / "source"
        source.write_text("x")
        os.link(source, payload / "copy")
        self.assertEqual(2, self.finish("hard"))

    def test_path_escape_and_symlinked_store_are_rejected(self):
        self.assertEqual(2, self.call("begin", "--project", str(self.project), "--task", "../escape",
                                      "--owner", "alice", "--reason", "bad"))
        other = Path(self.temp.name) / "other"
        other.mkdir()
        (self.project / ".agent-artifacts").symlink_to(other, target_is_directory=True)
        self.assertEqual(2, self.call("begin", "--project", str(self.project), "--task", "one",
                                      "--owner", "alice", "--reason", "bad"))

    def test_corrupt_metadata_is_skipped(self):
        self.begin()
        self.assertEqual(0, self.finish())
        self.expire()
        path = self.project / ".agent-artifacts" / "recent" / "one" / "metadata.json"
        path.write_text("not json")
        self.assertEqual(0, self.call("sweep", "--project", str(self.project), "--apply"))
        self.assertTrue(path.exists())

    def test_unknown_group_file_and_bad_expiry_are_skipped(self):
        self.begin("unknown")
        self.assertEqual(0, self.finish("unknown"))
        group = self.project / ".agent-artifacts" / "recent" / "unknown"
        (group / "unexpected").write_text("do not remove")
        self.expire("unknown")
        self.begin("bad-date")
        self.assertEqual(0, self.finish("bad-date"))
        path = self.project / ".agent-artifacts" / "recent" / "bad-date" / "metadata.json"
        data = json.loads(path.read_text())
        data["expires_at"] = "not-a-date"
        path.write_text(json.dumps(data))
        self.assertEqual(0, self.call("sweep", "--project", str(self.project), "--apply"))
        self.assertTrue((group / "unexpected").exists())
        self.assertTrue(path.exists())

    def test_remove_rechecks_fingerprint_immediately_before_deletion(self):
        payload = self.begin()
        (payload / "proof").write_text("before")
        self.assertEqual(0, self.finish())
        self.expire()
        group = self.project / ".agent-artifacts" / "recent" / "one"
        data = self.metadata()
        (group / "payload" / "proof").write_text("after")
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.remove_group(artifacts.store_for(self.project), group, "recent",
                                   data["fingerprint"], data["total_bytes"])
        self.assertTrue(group.exists())


if __name__ == "__main__":
    unittest.main()
