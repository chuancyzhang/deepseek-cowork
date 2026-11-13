"""Native process survival, conversation isolation and takeover through real RPC."""
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from test_desktop_service import ServiceClient, QA_ROOT


class TerminalTests(unittest.TestCase):
    def test_live_shell_isolated_and_survives_view_reopen(self):
        with tempfile.TemporaryDirectory(dir=QA_ROOT) as directory:
            client = ServiceClient(directory)
            try:
                cid = client.call('conversation.create')['id']
                other = client.call('conversation.create')['id']
                profiles = client.call('workbench.profiles', conversation_id=cid)
                self.assertTrue(profiles)
                tab = client.call('workbench.open', conversation_id=cid, shell=profiles[0]['id'])['tab']
                args = dict(conversation_id=cid, tab_id=tab['id'])
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    status = client.call('workbench.read', **args)
                    if status['status'] != 'starting':
                        break
                    time.sleep(.1)
                self.assertEqual(status['status'], 'running', status)
                client.call('workbench.input', **args, control_revision=tab['control_revision'], text='echo COWORK_NATIVE_TERMINAL_OK\r')
                output = ''
                cursor = 0
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    result = client.call('workbench.read', **args, cursor=cursor)
                    if '\x1b[6n' in result['output']:
                        client.call('workbench.reply', **args, text='\x1b[1;1R')
                    if '\x1b[c' in result['output'] or '\x1b[0c' in result['output']:
                        client.call('workbench.reply', **args, text='\x1b[?1;2c')
                    cursor = result['cursor']
                    output += result['output']
                    if output.count('COWORK_NATIVE_TERMINAL_OK') >= 2:
                        break
                    time.sleep(.1)
                self.assertGreaterEqual(output.count('COWORK_NATIVE_TERMINAL_OK'), 2, output)
                with self.assertRaisesRegex(RuntimeError, '协议应答'):
                    client.call('workbench.reply', **args, text='echo MUST_NOT_EXECUTE\r')
                with self.assertRaisesRegex(RuntimeError, '不属于'):
                    client.call('workbench.read', conversation_id=other, tab_id=tab['id'])
                self.assertEqual(client.call('workbench.list', conversation_id=cid)['tabs'][0]['id'], tab['id'])
                self.assertIn('COWORK_NATIVE_TERMINAL_OK', client.call('workbench.read', **args, cursor=0)['output'])
                client.call('workbench.control', **args, owner='available')
                with self.assertRaisesRegex(RuntimeError, '接管'):
                    client.call('workbench.input', **args, control_revision=tab['control_revision'], text='echo MUST_NOT_EXECUTE\r')
                client.call('workbench.close', **args)
                self.assertEqual(client.call('workbench.list', conversation_id=cid)['tabs'], [])
            finally:
                client.close()


def test_agent_cannot_write_after_user_takes_control():
    from core.desktop_workbench import DesktopWorkbench
    from core.workbench import WorkbenchError
    service = SimpleNamespace(request_agent=Mock(return_value={'status': 'ok'}), publish=Mock())
    workbench = DesktopWorkbench(service)
    tab = workbench.store.open('a', 'terminal')
    terminal = Mock()
    workbench.terminals[tab.id] = terminal
    workbench.store.set_owner('a', tab.id, 'human')
    try:
        workbench.agent_request({'session_id': 'a', 'run_id': 'r', 'request_id': 'q'}, {'token': 't', 'action': 'terminal_input', 'arguments': {'tab_id': tab.id, 'text': 'must not execute'}})
        raise AssertionError('human takeover must block agent input')
    except WorkbenchError as exc:
        assert exc.code == 'human_control'
    terminal.write.assert_not_called()
    service.request_agent.return_value = {'status': 'unavailable'}
    result = workbench.agent_request({'session_id': 'a'}, {'token': 't', 'action': 'terminal_input', 'arguments': {'tab_id': tab.id}})
    assert result['status'] == 'cancelled'
    terminal.write.assert_not_called()


def test_office_preview_job_is_read_only_versioned_and_conversation_scoped(tmp_path, monkeypatch):
    import hashlib
    import pytest
    from core.desktop_workbench import DesktopFiles
    from core import document_preview, libreoffice_component
    source = tmp_path / 'slides.pptx'
    source.write_bytes(b'original')
    rendered = tmp_path / 'cache.pdf'
    rendered.write_bytes(b'%PDF-fixture')
    storage = SimpleNamespace(get_conversation_record=lambda cid: {'meta': {'workspace_dir': str(tmp_path)}})
    jobs = SimpleNamespace(submit=Mock(return_value={'job_id': 'preview'}), get=Mock())
    files = DesktopFiles(storage, None, jobs)
    monkeypatch.setattr(libreoffice_component, 'status', lambda: {'available': True})
    before = source.read_bytes()
    opened = files.dispatch('file.open', {'conversation_id': 'a', 'path': str(source)})
    assert opened['payload']['preview_kind'] == 'office'
    assert opened['payload']['read_only'] and opened['payload']['office_family'] == 'powerpoint'
    convert = Mock(return_value=(str(rendered), opened['fingerprint']))
    monkeypatch.setattr(document_preview, 'render_office_pdf', convert)
    files.dispatch('file.render', {'conversation_id': 'a', 'path': str(source), 'fingerprint': opened['fingerprint']})
    import threading
    operation = jobs.submit.call_args.args[1]
    result = operation(threading.Event(), Mock())
    assert convert.call_args.kwargs['renderer'] == 'libreoffice'
    jobs.get.return_value = {'id': 'preview', 'status': 'completed', 'result': result}
    ready = files.dispatch('file.render_result', {'conversation_id': 'a', 'job_id': 'preview'})
    assert ready['path'] == str(source)
    assert ready['payload']['preview_path'] == str(rendered)
    assert source.read_bytes() == before
    with pytest.raises(ValueError, match='不属于'):
        files.dispatch('file.render_result', {'conversation_id': 'b', 'job_id': 'preview'})
    source.write_bytes(b'changed')
    with pytest.raises(ValueError, match='已变化'):
        operation(threading.Event(), Mock())
    assert convert.call_count == 1


def test_libreoffice_install_checks_package_before_starting_program(tmp_path, monkeypatch):
    import threading
    import pytest
    from core import libreoffice_component as component
    monkeypatch.setattr(component, 'component_root', lambda: tmp_path)
    monkeypatch.setattr(component, 'can_install', lambda: True)
    monkeypatch.setattr(component, 'managed_executable', lambda: None)
    start = Mock()
    monkeypatch.setattr(component, 'popen_external_program', start)
    package = tmp_path / 'bad.msi'
    package.write_bytes(b'invalid')
    with pytest.raises(ValueError, match='校验失败'):
        component.install(threading.Event(), Mock(), package=package)
    start.assert_not_called()
    assert package.read_bytes() == b'invalid'
    assert not list(tmp_path.glob('install-*'))
    from core.desktop_jobs import JobCancelled
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(JobCancelled):
        component.install(cancelled, Mock(), package=package)
    start.assert_not_called()


def test_old_word_draft_can_only_be_recovered_as_copy(tmp_path, monkeypatch):
    import base64
    import io
    import pytest
    from docx import Document
    from core.desktop_workbench import DesktopFiles
    from core import libreoffice_component
    source = tmp_path / 'draft.docx'
    original = Document()
    original.add_paragraph('原内容')
    original.save(source)
    before = source.read_bytes()
    storage = SimpleNamespace(get_conversation_record=lambda cid: {'meta': {'workspace_dir': str(tmp_path)}})
    files = DesktopFiles(storage, None)
    monkeypatch.setattr(libreoffice_component, 'status', lambda: {'available': False})
    opened = files.dispatch('file.open', {'conversation_id': 'a', 'path': str(source), 'recover_draft': True})
    assert opened['payload']['read_only'] and opened['payload']['recovery_available']
    edited = Document()
    edited.add_paragraph('恢复的未保存内容')
    binary = io.BytesIO()
    edited.save(binary)
    payload = {'kind': 'docx', 'value': base64.b64encode(binary.getvalue()).decode()}
    with pytest.raises(ValueError, match='另存副本'):
        files.dispatch('file.save', {'conversation_id': 'a', 'id': opened['id'], 'payload': payload})
    copied = files.dispatch('file.save_copy', {'conversation_id': 'a', 'id': opened['id'], 'name': 'recovered.docx', 'payload': payload})
    assert Document(copied['path']).paragraphs[0].text == '恢复的未保存内容'
    assert source.read_bytes() == before
    assert copied['payload']['read_only'] and not copied['payload']['recovery_available']


def test_office_preview_cleans_deep_extension_cache(tmp_path):
    from core.document_preview import _libreoffice_staging
    from core.platform_runtime import platform_runtime
    with _libreoffice_staging(tmp_path) as staging:
        nested = platform_runtime.filesystem_path(staging / ('extensions-' + 'a' * 60) / ('help-' + 'b' * 60) / ('cache-' + 'c' * 60))
        nested.mkdir(parents=True)
        (nested / 'payload.xml').write_text('cache', encoding='utf-8')
    assert not staging.exists()
