"""Git paths and Markdown boundaries that must not hide changes."""
import hashlib
import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('codd_boundaries', Path(__file__).resolve().parents[1] / 'machine/codd.py')
codd = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = codd
spec.loader.exec_module(codd)


class BoundaryRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git('init', '-q')
        self.side = codd.Side('own', self.repo, [])

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'baseline')

    @unittest.skipIf(os.name == "nt", "Windows forbids these filename characters")
    def test_dirty_paths_are_literal(self):
        names = ['a -> b.txt', 'tab\tname.txt', 'quote"name.txt', 'line\nname.txt']
        for name in names:
            (self.repo / name).write_bytes(b'content')
        self.assertEqual(codd.dirty_files(self.side), {n: hashlib.sha256(b'content').hexdigest() for n in names})

    def test_rename_records_deleted_source(self):
        (self.repo / 'old.py').write_bytes(b'content')
        self.commit()
        self.git('mv', 'old.py', 'new.py')
        self.assertEqual(codd.changed_since(self.side, {'head': codd.head(self.repo), 'files': {}}), {'old.py', 'new.py'})

    @unittest.skipIf(os.name == "nt", "Windows forbids newlines in filenames")
    def test_tracked_paths_are_literal(self):
        name = 'line\nname.py'
        (self.repo / name).write_bytes(b'content')
        self.git('add', '.')
        self.assertEqual(codd.tracked(self.side), [name])

    def test_tracked_failure_is_not_empty_success(self):
        with mock.patch.object(codd, 'run', return_value=(128, 'broken repository')):
            with self.assertRaises(codd.CoddError):
                codd.tracked(self.side)

    def test_stamp_tracks_staged_changes_before_first_commit(self):
        path = self.repo / 'new.py'
        path.write_bytes(b'first')
        self.git('add', '.')
        before = codd._stamp(self.repo)
        path.write_bytes(b'second')
        self.git('add', '.')
        self.assertNotEqual(before, codd._stamp(self.repo))

    def test_document_anchor_ignores_fenced_headings(self):
        (self.repo / 'guide.md').write_bytes(b'# Guide\n```md\n## Target\nexample\n```\n## Target\nreal\n~~~\n## Fake\n~~~\ncontinued\n## End\n')
        self.assertEqual(codd.doc_text(self.repo, 'guide.md#Target'), '## Target\nreal\n~~~\n## Fake\n~~~\ncontinued\n')

    def test_status_failure_is_not_empty_success(self):
        with mock.patch.object(codd, 'run', return_value=(128, 'broken repository')):
            with self.assertRaises(codd.CoddError):
                codd.dirty_files(self.side)

    def test_stamp_tracks_unstaged_changes_before_first_commit(self):
        path = self.repo / 'new.py'
        path.write_bytes(b'first')
        self.git('add', '.')
        before = codd._stamp(self.repo)
        path.write_bytes(b'second')
        self.assertNotEqual(before, codd._stamp(self.repo))

    @unittest.skipIf(os.name == "nt", "Windows forbids newlines in filenames")
    def test_stamp_tracks_untracked_path_with_newline(self):
        path = self.repo / 'new\nfile.py'
        path.write_bytes(b'first')
        before = codd._stamp(self.repo)
        path.write_bytes(b'second')
        self.assertNotEqual(before, codd._stamp(self.repo))

    def test_fence_requires_matching_character_and_length(self):
        (self.repo / 'guide.md').write_bytes(b'## Target\n````md\n```\n~~~\n## Fake\n````\nreal\n## End\n')
        self.assertEqual(codd.doc_text(self.repo, 'guide.md#Target'),
                         '## Target\n````md\n```\n~~~\n## Fake\n````\nreal\n')

    def test_committed_rename_records_deleted_source(self):
        (self.repo / 'old.py').write_bytes(b'content')
        self.commit()
        before = codd.snapshot(self.side)
        self.git('mv', 'old.py', 'new.py')
        self.commit()
        self.assertEqual(codd.changed_since(self.side, before), {'old.py', 'new.py'})
