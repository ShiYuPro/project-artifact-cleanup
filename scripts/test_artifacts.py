import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('artifacts', Path(__file__).with_name('artifacts.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.args = argparse.Namespace(project=str(self.project), task='demo', owner='owner',
                                       reason='test', keep_days=14, apply=False)
        self.payload = Path(a.begin(self.args)['payload'])
        (self.payload / 'sample.txt').write_text('original')

    def expire(self):
        a.finish(self.args)
        p = self.project / '.agent-artifacts/recent/demo/metadata.json'
        data = json.loads(p.read_text())
        data['expires_at'] = '2000-01-01T00:00:00Z'
        p.write_text(json.dumps(data))
        return p.parent

    def test_zero_day_preview_then_apply(self):
        self.args.keep_days = 0
        self.assertFalse(a.finish(self.args)['payload_deleted'])
        self.assertTrue(self.payload.exists())
        self.args.apply = True
        self.assertTrue(a.finish(self.args)['payload_deleted'])
        self.assertFalse(self.payload.exists())

    def test_expired_preview_then_delete(self):
        group = self.expire()
        preview = a.sweep(self.args)
        self.assertEqual(preview['candidates'], ['demo'])
        self.assertEqual(preview['candidate_bytes'], len(b'original'))
        self.assertEqual(preview['deleted_bytes'], 0)
        self.assertTrue(group.exists())
        self.args.apply = True
        applied = a.sweep(self.args)
        self.assertEqual(applied['deleted'], ['demo'])
        self.assertEqual(applied['deleted_bytes'], len(b'original'))

    def test_active_is_never_swept(self):
        self.args.apply = True
        self.assertEqual(a.sweep(self.args)['deleted'], [])
        self.assertTrue(self.payload.exists())

    def test_changed_payload_is_skipped(self):
        group = self.expire()
        (group/'payload/sample.txt').write_text('edited')
        self.args.apply = True
        self.assertEqual(a.sweep(self.args)['skipped'][0]['reason'], 'payload changed')

    def test_added_empty_directory_is_change(self):
        group = self.expire()
        (group/'payload/new-directory').mkdir()
        self.args.apply = True
        self.assertEqual(a.sweep(self.args)['deleted'], [])

    def test_pin_protects_expired(self):
        self.expire()
        a.pin(self.args)
        self.args.apply = True
        self.assertEqual(a.sweep(self.args)['skipped'][0]['reason'], 'pinned')

    def test_owner_mismatch(self):
        self.args.owner = 'other'
        with self.assertRaises(a.ArtifactError): a.finish(self.args)
        self.assertTrue(self.payload.exists())

    def test_link_is_refused(self):
        outside = self.root/'important.txt'
        outside.write_text('keep')
        (self.payload/'link').symlink_to(outside)
        self.args.keep_days = 0
        self.args.apply = True
        with self.assertRaises(a.ArtifactError): a.finish(self.args)
        self.assertEqual(outside.read_text(), 'keep')

    def test_restore_pins_and_never_overwrites(self):
        group = self.expire()
        self.args.destination = str(self.root/'recovered')
        result = a.restore(self.args)
        self.assertTrue(result['pinned'])
        self.assertEqual((self.root/'recovered/sample.txt').read_text(), 'original')
        with self.assertRaises(a.ArtifactError): a.restore(self.args)
        self.args.apply = True
        self.assertEqual(a.sweep(self.args)['deleted'], [])

    def test_restore_into_store_refused(self):
        self.expire()
        self.args.destination = str(self.project/'.agent-artifacts/recovered')
        with self.assertRaises(a.ArtifactError): a.restore(self.args)

    def test_unknown_group_entry_protected(self):
        group = self.expire()
        (group/'keep.txt').write_text('not managed')
        self.args.apply = True
        self.assertEqual(a.sweep(self.args)['deleted'], [])
        self.assertTrue((group/'keep.txt').exists())

    def test_custom_retention_parser(self):
        args = a.parser().parse_args(['finish','--project',str(self.project),'--task','demo',
                                     '--owner','owner','--keep-days','30'])
        self.assertEqual(args.keep_days, 30)

if __name__ == '__main__': unittest.main()
