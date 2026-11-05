"""Real persistence/release paths with synthetic payloads; no devices or network."""
from contextlib import contextmanager
import builtins
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cli_coordination
import filesystem_commit
import test_cli_release_store as linux_release
import test_native_cli as pinned_release
import test_native_cli_update as managed_release
import test_workspace_groups as workspace
import runtime_refresh
from device_termination import TerminationJournal
import test_runtime_refresh as runtime_tests
import test_mapping_library as mapping_tests
from mapping_library import MappingLibrary


def denied():
    error = PermissionError(13, 'Synthetic transient Windows access denial')
    error.winerror = 5
    return error


@contextmanager
def fixture(kind):
    case = kind(methodName='runTest')
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()


@unittest.skipUnless(os.name == 'nt', 'Windows file commit contention')
class ReleaseCommitFlows(unittest.TestCase):
    variants = (linux_release.CLIReleaseStoreTests,
                pinned_release.NativeCLIStoreTests,
                managed_release.ManagedNativeTests)

    @staticmethod
    def prepare(case):
        return case.prepared() if isinstance(case, managed_release.ManagedNativeTests) else case.prepare()

    @staticmethod
    def external_calls(case):
        return len(case.network.calls), len(getattr(case, 'git', getattr(case, 'runner', None)).calls)

    def test_transient_publish_reuses_validated_stage_without_external_work(self):
        for variant in self.variants:
            with self.subTest(variant=variant.__name__), fixture(variant) as case:
                original = os.replace
                attempts = []

                def move(source, target):
                    if Path(source).name.startswith('.prepare-'):
                        attempts.append(self.external_calls(case))
                        if len(attempts) == 1:
                            raise denied()
                    return original(source, target)

                with patch.object(os, 'replace', side_effect=move):
                    result = self.prepare(case)
                self.assertEqual(len(attempts), 2)
                self.assertEqual(attempts[0], attempts[1])
                self.assertEqual(result['status'], 'prepared')
                self.assertTrue((Path(result['directory']) / 'manifest.json').is_file())
                self.assertEqual(list(case.store.root.glob('.prepare-*')), [])
                self.assertFalse((case.store.root.parent / 'selection.json').exists())

    def test_cancel_during_denied_publish_never_repeats_preparation_or_commits(self):
        for variant in self.variants:
            with self.subTest(variant=variant.__name__), fixture(variant) as case:
                case.store.cancel_event = threading.Event()
                original = os.replace
                attempts = []

                def move(source, target):
                    if Path(source).name.startswith('.prepare-'):
                        attempts.append(self.external_calls(case))
                        case.store.cancel_event.set()
                        raise denied()
                    return original(source, target)

                with patch.object(os, 'replace', side_effect=move), self.assertRaisesRegex(ValueError, 'cancelled'):
                    self.prepare(case)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(self.external_calls(case), attempts[0])
                self.assertEqual(list(case.store.root.glob('.prepare-*')), [])
                self.assertEqual([p for p in case.store.root.iterdir() if p.is_dir()], [])

    def test_replaced_stage_is_preserved_and_never_published_or_deleted(self):
        for variant in self.variants:
            with self.subTest(variant=variant.__name__), fixture(variant) as case:
                original = os.replace
                replaced = []

                def move(source, target):
                    if Path(source).name.startswith('.prepare-'):
                        source = Path(source)
                        aside = source.with_name('retained-original-stage')
                        original(source, aside)
                        source.mkdir()
                        (source / 'other-owner').write_bytes(b'KEEP REPLACEMENT')
                        replaced.append((source, aside, Path(target)))
                        raise denied()
                    return original(source, target)

                with patch.object(os, 'replace', side_effect=move), self.assertRaises(filesystem_commit.CommitConflict):
                    self.prepare(case)
                self.assertEqual(len(replaced), 1)
                source, aside, target = replaced[0]
                self.assertEqual((source / 'other-owner').read_bytes(), b'KEEP REPLACEMENT')
                self.assertTrue((aside / 'manifest.json').is_file())
                self.assertFalse(target.exists())


@unittest.skipUnless(os.name == 'nt', 'Windows file commit contention')
class WorkspaceCommitFlows(unittest.TestCase):
    def test_commit_then_denied_reply_keeps_disk_and_memory_in_agreement(self):
        with fixture(workspace.WorkspaceGroupsTest) as case:
            original = os.replace
            calls = []

            def move(source, target):
                calls.append((source, target))
                original(source, target)
                raise denied()

            with patch.object(os, 'replace', side_effect=move):
                ident = case.make_ws('Saved once')
            self.assertEqual(len(calls), 1)
            self.assertEqual(case.reload().rows, case.groups.rows)
            self.assertEqual(sum(r['id'] == ident for r in case.groups.rows), 1)
            self.assertEqual(list(case.folder.glob('*.pending')), [])

    def test_replacement_writer_is_not_overwritten_and_pending_evidence_survives(self):
        with fixture(workspace.WorkspaceGroupsTest) as case:
            case.make_ws('Baseline')
            before = json.loads(json.dumps(case.groups.rows))
            replacement = [dict(id='other', label='Other owner', members=[])]
            incoming = case.folder / 'external-candidate'
            incoming.write_text(json.dumps(replacement), encoding='utf8')
            original = os.replace
            calls = []

            def move(source, target):
                calls.append((source, target))
                original(incoming, target)
                raise denied()

            with patch.object(os, 'replace', side_effect=move), self.assertRaises(filesystem_commit.CommitConflict):
                case.make_ws('Must not overwrite replacement')
            self.assertEqual(len(calls), 1)
            self.assertEqual(case.groups.rows, before)
            self.assertEqual(json.loads(case.groups.path.read_text(encoding='utf8')), replacement)
            pending = list(case.folder.glob('*.pending'))
            self.assertEqual(len(pending), 1)
            self.assertTrue(any(r['label'] == 'Must not overwrite replacement'
                                for r in json.loads(pending[0].read_text(encoding='utf8'))))
            with patch.object(os, 'replace', side_effect=AssertionError('Stale workspace wrote again')):
                with self.assertRaises(filesystem_commit.CommitConflict):
                    case.make_ws('Explicit retry from stale memory')
                with self.assertRaises(filesystem_commit.CommitConflict):
                    case.groups.save()
            self.assertEqual(case.groups.rows, before)
            self.assertTrue(case.groups.persistence_error)
            self.assertEqual(json.loads(case.groups.path.read_text(encoding='utf8')), replacement)
            self.assertEqual(list(case.folder.glob('*.pending')), pending)
            reopened = case.reload()
            reopened.action(dict(name='workspace_create', label='Created after re-reading'))
            saved = json.loads(case.groups.path.read_text(encoding='utf8'))
            self.assertEqual(saved[0], replacement[0])
            self.assertEqual(saved[-1]['label'], 'Created after re-reading')

    def test_write_failure_cleans_only_its_own_unique_candidate(self):
        with fixture(workspace.WorkspaceGroupsTest) as case:
            foreign = case.folder / 'unrelated.pending'
            foreign.write_bytes(b'KEEP')
            with patch.object(cli_coordination.json, 'dump', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    case.make_ws('Unsaved')
            self.assertEqual(foreign.read_bytes(), b'KEEP')
            self.assertEqual(list(case.folder.glob('*.pending')), [foreign])
            self.assertFalse(case.groups.path.exists())
            self.assertEqual(case.groups.rows, [dict(id='main', label='主工作区 / Main workspace', members=['main'])])


class StandaloneGuestCommit(unittest.TestCase):
    def test_posix_guest_imports_no_helper_and_calls_original_rename_once(self):
        original_import = builtins.__import__
        posix_os = SimpleNamespace(**{**vars(os), 'name': 'posix'})

        def guest_import(name, *args, **kwargs):
            if name == 'filesystem_commit':
                raise AssertionError('The single-file Linux guest must not import a host helper')
            if name == 'os':
                return posix_os
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'runtime_refresh.py'
            script.write_bytes(Path(runtime_refresh.__file__).read_bytes())
            namespace = {'__name__': 'standalone_guest', '__file__': str(script),
                         '__builtins__': {**vars(builtins), '__import__': guest_import}}
            exec(compile(script.read_bytes(), str(script), 'exec'), namespace)
        self.assertFalse(namespace['_WINDOWS'])
        source = SimpleNamespace(rename=Mock(side_effect=denied()))
        with self.assertRaises(PermissionError):
            namespace['_rename'](source, 'target')
        source.rename.assert_called_once_with('target')


@unittest.skipUnless(os.name == 'nt', 'Windows mapping library commits')
class MappingCommitFlows(unittest.TestCase):
    def test_retry_and_committed_reply_do_not_increment_revision_twice(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'mappings.json'
                library = MappingLibrary(path)
                original = os.replace
                calls = []

                def move(source, target):
                    calls.append((source, target))
                    if len(calls) == 1:
                        if committed:
                            original(source, target)
                        raise denied()
                    return original(source, target)

                with patch.object(os, 'replace', side_effect=move):
                    saved = library.save(mapping_tests.bind(), mapping_tests.cfg())
                self.assertEqual(saved['revision'], 1)
                self.assertEqual(library.snapshot(mapping_tests.bind())['revision'], 1)
                self.assertEqual(len(calls), 1 if committed else 2)
                self.assertEqual(list(Path(tmp).glob('*.tmp')), [])

    def test_failed_save_keeps_revision_and_old_bytes_for_explicit_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mappings.json'
            library = MappingLibrary(path)
            library.save(mapping_tests.bind(), mapping_tests.cfg())
            before = path.read_bytes()
            with patch.object(os, 'replace', side_effect=denied()) as move:
                with self.assertRaises(PermissionError):
                    library.save(mapping_tests.bind(), mapping_tests.cfg(gain=2))
            self.assertEqual(move.call_count, 10)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(library.snapshot(mapping_tests.bind())['revision'], 1)
            self.assertEqual(library.save(mapping_tests.bind(), mapping_tests.cfg(gain=2))['revision'], 2)

    def test_next_operation_rereads_replacement_and_preserves_other_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mappings.json'
            library = MappingLibrary(path)
            other = MappingLibrary(Path(tmp) / 'external.json')
            other.save(mapping_tests.bind(sdk_user='foreign'), mapping_tests.cfg())
            original = os.replace

            def move(source, target):
                original(other.path, target)
                raise denied()

            with patch.object(os, 'replace', side_effect=move) as primitive:
                with self.assertRaises(filesystem_commit.CommitConflict):
                    library.save(mapping_tests.bind(), mapping_tests.cfg())
            self.assertEqual(primitive.call_count, 1)
            pending = list(Path(tmp).glob('*.tmp'))
            self.assertEqual(len(pending), 1)
            self.assertEqual(len(library.list_entries()), 1)
            library.save(mapping_tests.bind(), mapping_tests.cfg())
            self.assertEqual(len(library.list_entries()), 2)
            self.assertTrue(library.snapshot(mapping_tests.bind(sdk_user='foreign'))['saved'])
            self.assertEqual(list(Path(tmp).glob('*.tmp')), pending)


@unittest.skipUnless(os.name == 'nt', 'Windows stop journal commits')
class TerminationJournalCommitFlows(unittest.TestCase):
    def test_transient_denial_retries_only_the_same_session_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = TerminationJournal(Path(tmp) / 'device_termination.json')
            original = os.replace
            calls = []

            def move(source, target):
                calls.append((source, target))
                if len(calls) == 1:
                    raise denied()
                return original(source, target)

            with patch.object(os, 'replace', side_effect=move):
                self.assertEqual(journal.new_session(), 1)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(journal.next_session, 2)
            self.assertFalse(journal.dirty)
            self.assertEqual(TerminationJournal(journal.path).next_session, 2)
            self.assertEqual(list(journal.path.parent.glob('*.partial')), [])

    def test_committed_record_with_failed_reply_does_not_reuse_session_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = TerminationJournal(Path(tmp) / 'device_termination.json')
            original = os.replace

            def move(source, target):
                original(source, target)
                raise denied()

            with patch.object(os, 'replace', side_effect=move) as primitive:
                self.assertEqual(journal.new_session(), 1)
            self.assertEqual(primitive.call_count, 1)
            self.assertFalse(journal.dirty)
            self.assertEqual(TerminationJournal(journal.path).new_session(), 2)

    def test_persistent_denial_keeps_uncertainty_dirty_and_original_disk_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = TerminationJournal(Path(tmp) / 'device_termination.json')
            session = journal.new_session()
            before = journal.path.read_bytes()
            with patch.object(os, 'replace', side_effect=denied()) as primitive:
                with self.assertRaises(PermissionError):
                    journal.observe('hand', 'SYNTHETIC-HAND', dict(active=True), session)
            self.assertEqual(primitive.call_count, 10)
            self.assertTrue(journal.dirty)
            self.assertEqual(journal.path.read_bytes(), before)
            self.assertEqual(len(journal.rows), 1)
            self.assertEqual(list(journal.path.parent.glob('*.partial')), [])
            journal.save()
            self.assertFalse(journal.dirty)
            self.assertEqual(TerminationJournal(journal.path).rows, journal.rows)

    def test_replacement_journal_and_partial_evidence_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = TerminationJournal(Path(tmp) / 'device_termination.json')
            journal.new_session()
            foreign = Path(tmp) / 'external'
            replacement = dict(version=1, next_session=100, confirmed={}, rows=[
                dict(source='hand', key='OTHER-HAND', device_id='OTHER-HAND', session=99,
                     reason='Unconfirmed stop belonging to the replacement')])
            foreign.write_text(json.dumps(replacement), encoding='utf8')
            foreign_bytes = foreign.read_bytes()
            original = os.replace

            def move(source, target):
                original(foreign, target)
                raise denied()

            with patch.object(os, 'replace', side_effect=move) as primitive:
                with self.assertRaises(filesystem_commit.CommitConflict):
                    journal.new_session()
            self.assertEqual(primitive.call_count, 1)
            self.assertTrue(journal.dirty)
            self.assertEqual(journal.path.read_bytes(), foreign_bytes)
            partial = list(Path(tmp).glob('*.partial'))
            self.assertEqual(len(partial), 1)
            self.assertEqual(json.loads(partial[0].read_text(encoding='utf8'))['next_session'], 3)
            epoch = journal.next_session
            with patch.object(os, 'replace', side_effect=AssertionError('Stale journal wrote again')):
                visible = journal.snapshot()
                self.assertTrue(any(r.get('source') == 'journal' for r in visible))
                with self.assertRaises(filesystem_commit.CommitConflict):
                    journal.save()
                with self.assertRaises(filesystem_commit.CommitConflict):
                    journal.new_session()
            self.assertEqual(journal.next_session, epoch)
            self.assertEqual(journal.path.read_bytes(), foreign_bytes)
            self.assertEqual(list(Path(tmp).glob('*.partial')), partial)
            # A new instance explicitly re-reads the replacement's high epoch
            # and unconfirmed device; it never resumes this stale memory copy.
            reopened = TerminationJournal(journal.path)
            self.assertEqual(reopened.snapshot(), replacement['rows'])
            self.assertEqual(reopened.new_session(), 100)
            self.assertEqual(reopened.rows, replacement['rows'])
            newer = journal.path.read_bytes()
            journal.snapshot()
            self.assertEqual(journal.path.read_bytes(), newer)


@unittest.skipUnless(os.name == 'nt', 'Windows synthetic runtime commits')
class RuntimeCommitFlows(unittest.TestCase):
    def test_one_denial_at_publish_and_at_rollback_preserves_component_contracts(self):
        for rollback in (False, True):
            with self.subTest(rollback=rollback), tempfile.TemporaryDirectory() as tmp:
                case = runtime_tests.RuntimeRefreshTests()
                image, digest = case.image(tmp)
                root = case.runtime(tmp)
                current = root / runtime_refresh.ENV
                original = Path.rename
                attempts = []

                def rename(source, target):
                    wanted = '.backup-' if rollback else '.pending-'
                    if Path(target) == current and wanted in source.name:
                        attempts.append((source, target))
                        if len(attempts) == 1:
                            raise denied()
                    return original(source, target)

                responses = [SimpleNamespace(), SimpleNamespace(stdout='wuji ' + runtime_refresh.SDK_VERSION),
                             subprocess.CalledProcessError(1, ['synthetic final verification']) if rollback else SimpleNamespace()]
                with patch.object(runtime_refresh.venv, 'EnvBuilder') as builder, \
                        patch.object(runtime_refresh.subprocess, 'run', side_effect=responses) as run, \
                        patch.object(Path, 'rename', rename):
                    builder.return_value.create.side_effect = case.stage
                    if rollback:
                        with self.assertRaises(subprocess.CalledProcessError):
                            runtime_refresh.apply(image, digest, root)
                    else:
                        result = runtime_refresh.apply(image, digest, root)
                self.assertEqual(len(attempts), 2)
                self.assertEqual(run.call_count, 3)
                self.assertEqual((root / 'home/workbench/.wuji/private').read_bytes(), b'PERSONAL')
                if rollback:
                    self.assertEqual((current / 'old-sdk').read_text(), 'retained old')
                    self.assertEqual((root / runtime_refresh.CLI).read_bytes(), b'old CLI')
                    self.assertEqual(list(root.rglob('*.backup-*')), [])
                else:
                    self.assertEqual((Path(result['sdk_backup']) / 'old-sdk').read_text(), 'retained old')
                    self.assertEqual(Path(result['cli_backup']).read_bytes(), b'old CLI')
                self.assertEqual(list(root.rglob('*.pending-*')), [])

    def test_rollback_never_moves_a_replacement_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = runtime_tests.RuntimeRefreshTests()
            image, digest = case.image(tmp)
            root = case.runtime(tmp)
            current = root / runtime_refresh.ENV
            retained = current.with_name('retained-new-runtime')
            calls = []

            def run(*args, **kwargs):
                calls.append(args)
                if len(calls) == 2:
                    return SimpleNamespace(stdout='wuji ' + runtime_refresh.SDK_VERSION)
                if len(calls) == 3:
                    current.rename(retained)
                    current.mkdir()
                    (current / 'other-owner').write_bytes(b'KEEP OTHER RUNTIME')
                    raise subprocess.CalledProcessError(1, ['synthetic final verification'])
                return SimpleNamespace()

            with patch.object(runtime_refresh.venv, 'EnvBuilder') as builder, \
                    patch.object(runtime_refresh.subprocess, 'run', side_effect=run):
                builder.return_value.create.side_effect = case.stage
                with self.assertRaisesRegex(RuntimeError, 'restoration is unconfirmed'):
                    runtime_refresh.apply(image, digest, root)
            self.assertEqual((current / 'other-owner').read_bytes(), b'KEEP OTHER RUNTIME')
            self.assertTrue((retained / 'lib/python3.12/site-packages/wuji_sdk/__init__.py').is_file())
            backups = list(current.parent.glob(current.name + '.backup-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'old-sdk').read_text(), 'retained old')


if __name__ == '__main__':
    unittest.main()
