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

def test_html_visual_draft_preview_and_save_preserve_scripts_and_head(tmp_path):
    from core.desktop_workbench import DesktopFiles
    import pytest
    source = tmp_path / 'deck.html'
    original = '<!doctype html><html><head><style>h1{color:red}</style></head><body><h1>Original</h1><script>window.fixture=7;</script></body></html>'
    source.write_text(original, encoding='utf-8')
    storage = SimpleNamespace(get_conversation_record=lambda cid: {'meta':{'workspace_dir':str(tmp_path)}})
    files = DesktopFiles(storage,None)
    opened = files.dispatch('file.open',{'conversation_id':'a','path':str(source)})
    edited = opened['payload']['content'].replace('Original','Edited')
    assert 'window.fixture=7' not in edited
    params = {'conversation_id':'a','id':opened['id'],'payload':edited}
    preview = files.dispatch('file.preview',params)['payload']['preview_html']
    assert 'Edited' in preview and 'window.fixture=7' in preview and 'h1{color:red}' in preview
    assert source.read_text()==original
    with pytest.raises(ValueError,match='编辑会话已失效'):
        files.dispatch('file.preview',{**params,'conversation_id':'b'})
    files.dispatch('file.save',params)
    assert 'Edited' in source.read_text() and 'window.fixture=7' in source.read_text()
    with pytest.raises(Exception,match='缺少受保护'):
        files.dispatch('file.preview',{**params,'payload':'<html><body>broken</body></html>'})

def test_browser_unknown_receipt_blocks_only_its_tab_and_allows_inspection(tmp_path):
    import threading
    from core.chat_storage import ChatStorage
    from core.runtime_ledger import RuntimeLedger
    from core.desktop_workbench import DesktopWorkbench
    storage = ChatStorage(str(tmp_path / "history.sqlite"))
    ledger = RuntimeLedger(storage)
    events = []
    service = SimpleNamespace(ledger=ledger, storage=storage, request_agent=Mock(return_value={'status': 'ok'}), publish=events.append)
    workbench = DesktopWorkbench(service)
    browser = workbench.browser
    tab = workbench.store.open('chat', 'web', url='https://example.test')
    workbench.store.set_owner('chat', tab.id, 'agent')
    request = {'event': threading.Event(), 'cid': 'chat', 'tab_id': tab.id, 'revision': tab.control_revision,
               'identity': {'run_id': 'run'}, 'deadline': time.monotonic()+10, 'result': None}
    browser.pending['first'] = request
    try:
        receipt = browser.dispatch('browser.host_dispatch', {'id': 'first', 'action': 'click', 'step': 0})
        assert browser.unresolved('chat', tab.id)['effect_state'] == 'pending'
        browser.dispatch('browser.host_receipt', {'id': 'first', 'result': {'receipt_id': receipt['receipt_id'], 'status': 'unknown', 'effect_state': 'unknown', 'executed': True}})
        browser.pending['second'] = {**request, 'receipt': None}
        import pytest
        with pytest.raises(Exception, match='尚未核实'):
            browser.dispatch('browser.host_dispatch', {'id': 'second', 'action': 'click', 'step': 0})
        # Read-only work remains possible and cannot clear the durable blocker.
        browser.dispatch('browser.host_check', {'id': 'second'})
        assert browser.unresolved('chat', tab.id)['receipt_id'] == receipt['receipt_id']
        before = ledger.record('browser/chat/actions/'+receipt['receipt_id'])
        assert before['effect_state'] == 'unknown'
        # Another tab in the SAME chat remains usable; its own unresolved
        # receipt must not overwrite the first tab's blocker.
        other = workbench.store.open('chat', 'web')
        browser.pending['other'] = {**request, 'tab_id': other.id, 'revision': other.control_revision, 'identity': None, 'receipt': None}
        assert browser.dispatch('browser.host_dispatch', {'id': 'other', 'action': 'click'})['ok']
        browser.dispatch('browser.host_receipt', {'id': 'other', 'result': {'status': 'unknown', 'effect_state': 'unknown'}})
        assert browser.unresolved('chat', other.id)['effect_state'] == 'unknown'
        for action in ('scroll', 'scroll_into_view', 'hover'):
            assert browser.dispatch('browser.host_dispatch', {'id': 'second', 'action': action})['ok']
            browser.dispatch('browser.host_receipt', {'id': 'second', 'result': {'status': 'dispatched', 'effect_state': 'dispatched', 'executed': True}})
            assert browser.unresolved('chat', tab.id)['receipt_id'] == receipt['receipt_id']
        with pytest.raises(Exception, match='没有匹配'):
            browser.execute('chat', 'browser_action', {'tab_id': other.id, 'action': 'resolve_unknown', 'receipt_id': receipt['receipt_id'], 'resolution': 'completed'}, {'run_id': 'run'})
        reconciled = browser.execute('chat', 'browser_action', {'tab_id': tab.id, 'action': 'resolve_unknown', 'receipt_id': receipt['receipt_id'], 'resolution': 'completed'}, {'run_id': 'run'})
        assert reconciled['status'] == 'reconciled'
        assert browser.unresolved('chat', tab.id) == {}
        assert browser.unresolved('chat', other.id)['effect_state'] == 'unknown'
        assert ledger.record('browser/chat/actions/'+receipt['receipt_id'])['effect_state'] == 'completed'
        browser.dispatch('browser.host_dispatch', {'id': 'second', 'action': 'click', 'step': 0})
        # A host disappearing after intent registration remains unknown after restart.
        ledger.close()
        ledger = RuntimeLedger(storage)
        service.ledger = ledger
        assert browser.unresolved('chat', tab.id)['effect_state'] == 'pending'
        with pytest.raises(Exception, match='尚未核实'):
            browser.dispatch('browser.host_dispatch', {'id': 'second', 'action': 'click', 'step': 0})
    finally:
        ledger.close()


def test_browser_legacy_unknown_can_be_resolved_with_next_action(tmp_path):
    from core.chat_storage import ChatStorage
    from core.runtime_ledger import RuntimeLedger
    from core.desktop_workbench import DesktopWorkbench
    ledger = RuntimeLedger(ChatStorage(str(tmp_path / 'history.sqlite')))
    service = SimpleNamespace(ledger=ledger, request_agent=Mock(return_value={'status': 'ok'}))
    workbench = DesktopWorkbench(service)
    browser = workbench.browser
    tab = workbench.store.open('chat', 'web')
    workbench.store.set_owner('chat', tab.id, 'agent')
    legacy = {'receipt_id': 'old:0', 'tab_id': tab.id, 'action': 'click', 'effect_state': 'unknown'}
    ledger.append('chat', [{'kind': 'record', 'key': 'browser/chat/unresolved', 'value': legacy}])
    dispatched = []
    lose_result = False

    def respond(event):
        if event.get('event') != 'browser_host':
            return
        key = event['data']['id']
        dispatched.append(browser.dispatch('browser.host_dispatch', {'id': key, 'action': 'click'}))
        result = {'ok': True, 'status': 'dispatched', 'effect_state': 'dispatched', 'executed': True}
        browser.dispatch('browser.host_receipt', {'id': key, 'result': result})
        if lose_result:
            browser.pending[key]['deadline'] = time.monotonic() - 1
        else:
            browser.dispatch('browser.host_result', {'id': key, 'result': result})

    service.publish = respond
    try:
        other = workbench.store.open('chat', 'web')
        assert browser.unresolved('chat', other.id) == {}
        result = browser.execute('chat', 'browser_action', {'tab_id': tab.id, 'action': 'click',
            'receipt_id': 'old:0', 'resolution': 'completed'}, {'run_id': 'run'})
        assert result['ok'] and len(dispatched) == 1
        assert result['reconciled_action']['effect_state'] == 'completed'
        assert browser.unresolved('chat', tab.id) == {}
        assert ledger.record('browser/chat/actions/old:0')['effect_state'] == 'completed'
        # Losing the final observation/result cannot undo a durable dispatch receipt.
        lose_result = True
        result = browser.execute('chat', 'browser_action', {'tab_id': tab.id, 'action': 'click'}, {'run_id': 'run'})
        assert result['status'] == 'incomplete' and result['effect_state'] == 'dispatched'
        assert result['executed'] and len(dispatched) == 2
        assert browser.unresolved('chat', tab.id) == {}
    finally:
        ledger.close()


def test_builtin_browser_image_uses_conversation_attachment_pipeline(tmp_path):
    import base64
    import io
    from PIL import Image
    from core.desktop_workbench import DesktopWorkbench
    from core.tool_images import tool_image_content_parts
    stream = io.BytesIO()
    Image.new('RGB', (10, 6), 'green').save(stream, format='PNG')
    service = SimpleNamespace(config=SimpleNamespace(get_chat_history_dir=lambda: str(tmp_path)), publish=Mock())
    workbench = DesktopWorkbench(service)
    workbench.browser.execute = Mock(return_value={'ok': True, 'screenshot': {'image_url': {'url': 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()}}, 'image_status': 'ready'})
    result = workbench.agent_request({'session_id': 'chat', 'run_id': 'run'}, {'token': 'token', 'action': 'observe', 'arguments': {}, 'policy': {'browser_backend': {'backend': 'internal'}}, 'model_supports_vision': True})
    parts = tool_image_content_parts(result)
    assert len(parts) == 1 and parts[0]['width'] == 10 and parts[0]['height'] == 6
    assert 'screenshot' not in result
    from pathlib import Path
    assert Path(parts[0]['path']).is_file()


def test_workspace_changes_read_index_and_saved_files_inside_conversation_directory(tmp_path, monkeypatch):
    import os
    import subprocess
    from pathlib import Path

    import pytest

    from core.desktop_workbench import DesktopFiles
    from core.sandbox_runtime import get_workspace_git
    from core.workbench import WorkbenchError

    git = get_workspace_git()
    if not git:
        pytest.skip('Git runtime is required for this integration test')
    if os.name == 'nt':
        monkeypatch.setenv('PATH', str(Path(os.environ['SystemRoot']) / 'System32'))
        assert get_workspace_git() == git
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path.parent))
    root = tmp_path / 'repository'
    folder = root / '报告'
    folder.mkdir(parents=True)

    def run(*args):
        subprocess.run([git, *args], cwd=root, check=True, capture_output=True)

    run('init', '--quiet', '--template=')
    run('config', 'core.autocrlf', 'false')
    source = folder / '[报告] totals.py'
    source.write_text('total = 1\nprint(total)\n', encoding='utf-8')
    deleted = folder / 'old.txt'
    deleted.write_bytes(b'old text\n')
    outside = root / 'outside.txt'
    outside.write_bytes(b'outside\n')
    (root / '.gitignore').write_bytes(b'ignored.txt\n')
    run('add', '--all')
    storage = SimpleNamespace(get_conversation_record=lambda cid: {'meta': {'workspace_dir': str(folder if cid == 'report' else tmp_path)}})
    files = DesktopFiles(storage, None)

    def read(method='workspace.changes', **params):
        return files.dispatch(method, {'conversation_id': 'report', **params})

    unborn = read()
    assert unborn['repository'] and {item['path'] for item in unborn['files']} == {source.name, deleted.name}
    run('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '--quiet', '-m', 'Baseline')
    source.write_text('total = 2\nprint(total)\n', encoding='utf-8')
    run('add', '--', source.relative_to(root).as_posix())
    source.write_text('total = 2\nprint(total)\n# 已更新\n', encoding='utf-8')
    outside.write_bytes(b'changed outside the conversation\n')
    deleted.unlink()
    (folder / 'new.txt').write_bytes(b'first line\nsecond line')
    (folder / 'ignored.txt').write_bytes(b'ignored\n')
    (folder / 'binary.dat').write_bytes(b'\x00\x01')
    index = (root / '.git' / 'index').read_bytes()

    rows = {item['path']: item for item in read()['files']}
    assert set(rows) == {source.name, deleted.name, 'new.txt', 'binary.dat'}
    assert [(item['status'], item['added'], item['removed']) for item in rows.values()] == [
        ('modified', 2, 1), ('added', None, None), ('added', 2, 0), ('deleted', 0, 1),
    ]
    patch = read('workspace.diff', path=str(source))['patch']
    assert '-total = 1' in patch and '+total = 2' in patch and '+# 已更新' in patch
    assert '+second line' in read('workspace.diff', path='new.txt')['patch']
    assert '-old text' in read('workspace.diff', path=str(deleted))['patch']
    assert files.dispatch('workspace.changes', {'conversation_id': 'plain'}) == {'repository': False, 'files': []}
    with pytest.raises(WorkbenchError, match='文件不属于当前工作目录'):
        read('workspace.diff', path=str(outside))
    assert (root / '.git' / 'index').read_bytes() == index


def test_turn_file_history_uses_actual_write_baseline_and_survives_next_turn(tmp_path):
    from core.turn_files import TurnFileStore, capture_owner
    from core.filesystem_ops import _atomic_write_bytes
    from core.desktop_workbench import DesktopFiles
    import copy
    import pytest
    root = tmp_path / 'project'
    root.mkdir()
    history = tmp_path / 'history'
    config = SimpleNamespace(get_chat_history_dir=lambda: str(history))
    source = root / 'task.py'
    source.write_bytes(b'user uncommitted\n')
    with capture_owner(config, 'a', 'first', root):
        _atomic_write_bytes(str(source), b'first result\n', existed_before=True)
        _atomic_write_bytes(str(source), b'final result\n', existed_before=True)
    store = TurnFileStore(history)
    first = store.list('a')[0]
    detail = store.read('a', first['id'])
    assert '-user uncommitted' in detail['patch'] and '+final result' in detail['patch']
    assert 'first result' not in detail['patch']
    assert first['added'] == first['removed'] == 1
    with capture_owner(config, 'a', 'second', root):
        _atomic_write_bytes(str(source), b'next turn\n', existed_before=True)
    assert store.read('a', first['id']) == detail
    with capture_owner(config, 'b', 'other', root):
        _atomic_write_bytes(str(source), b'other conversation\n', existed_before=True)
    assert len(store.list('b')) == 1 and len(store.list('a')) == 2
    with pytest.raises(ValueError, match='不属于'):
        store.read('b', first['id'])
    files = DesktopFiles(SimpleNamespace(get_conversation_record=lambda cid: {'meta': {'workspace_dir': str(root)}}), config)
    messages = [{'id': 'reply', 'role': 'assistant', 'content': '[脚本](task.py)', 'meta': {'turn_id': 'first'}}]
    original = copy.deepcopy(messages)
    displayed = files.message_files('a', messages)
    assert displayed[0]['turn_files'][0]['id'] == first['id']
    assert len(displayed[0]['file_references']) == 1
    source.unlink()
    reopened = DesktopFiles(files.storage, config).message_files('a', messages + [{'id': 'new', 'role': 'user', 'content': '解释一下'}])
    assert reopened[0]['file_references'] == displayed[0]['file_references']
    assert reopened[0]['turn_files'] == displayed[0]['turn_files']
    assert messages == original  # Provider history never acquires UI records.


def test_turn_history_partial_patch_and_move_delete_are_recorded(tmp_path):
    from core.turn_files import TurnFileStore, capture_owner
    from core.apply_patch import apply_patch
    from core.filesystem_ops import read_text_file, rename_path, delete_path
    config = SimpleNamespace(get_chat_history_dir=lambda: str(tmp_path / 'history'), get_god_mode=lambda: True)
    root = tmp_path / 'project'
    root.mkdir()
    source = root / 'old.txt'
    source.write_bytes(b'old\n')
    context = {'config_manager': config}
    read_text_file(str(root), 'old.txt', context=context)
    with capture_owner(config, 'a', 'run', root):
        result = apply_patch(str(root), '*** Begin Patch\n*** Update File: old.txt\n@@\n-old\n+new\n*** Add File: backup.txt\n+old\n*** End Patch', context=context)
        assert result['ok'], result
        assert rename_path(str(root), 'old.txt', 'moved.txt', context=context)['ok']
        assert delete_path(str(root), 'backup.txt', context=context, confirm_callback=lambda *_: True)['ok']
    records = TurnFileStore(tmp_path / 'history').list('a')
    assert {item['name']: item['status'] for item in records} == {'old.txt': 'deleted', 'moved.txt': 'added'}


def test_turn_file_reverted_writes_have_no_net_change(tmp_path):
    from core.turn_files import TurnFileStore, capture_owner
    from core.filesystem_ops import _atomic_write_bytes
    config = SimpleNamespace(get_chat_history_dir=lambda: str(tmp_path / 'history'))
    source = tmp_path / 'file.txt'
    source.write_bytes(b'original\n')
    with capture_owner(config, 'chat', 'run', tmp_path):
        _atomic_write_bytes(str(source), b'changed\n', existed_before=True)
        _atomic_write_bytes(str(source), b'original\n', existed_before=True)
    store = TurnFileStore(tmp_path / 'history')
    assert store.list('chat') == []
    with capture_owner(config, 'chat', 'run', tmp_path):
        _atomic_write_bytes(str(source), b'final\n', existed_before=True)
    record = store.list('chat')[0]
    assert '-original' in store.read('chat', record['id'])['patch']


def test_turn_file_capture_does_not_attribute_failed_operations_or_shell_output(tmp_path):
    import pytest
    from core.turn_files import TurnFileStore, capture_owner, capture_mutation
    from core.filesystem_ops import _atomic_write_bytes
    config = SimpleNamespace(get_chat_history_dir=lambda: str(tmp_path / 'history'))
    source = tmp_path / 'file.txt'
    source.write_bytes(b'before\n')
    with capture_owner(config, 'a', 'run', tmp_path):
        with pytest.raises(OSError), capture_mutation(source):
            raise OSError('denied before commit')
        # Opaque scripts do not opt in, so a path or a timestamp is not evidence.
        source.write_bytes(b'opaque script or external edit\n')
    store = TurnFileStore(tmp_path / 'history')
    assert store.list('a') == []
    with capture_owner(config, 'a', 'run', tmp_path):
        _atomic_write_bytes(str(source), b'known write\n', existed_before=True)
    source.write_bytes(b'external concurrent edit\n')
    with capture_owner(config, 'a', 'run', tmp_path):
        _atomic_write_bytes(str(source), b'known write again\n', existed_before=True)
    record = store.list('a')[0]
    assert record['state'] == 'baseline_missing' and record['added'] is None
    assert store.read('a', record['id'])['patch'] == ''


def test_history_quota_reclaims_only_finished_bodies_and_preserves_statistics(tmp_path):
    from core.turn_files import TurnFileStore, capture_owner
    from core.filesystem_ops import _atomic_write_bytes
    config = SimpleNamespace(get_chat_history_dir=lambda: str(tmp_path / 'history'))
    original = tmp_path / 'original.txt'
    original.write_bytes(b'unrelated attachment')
    source = tmp_path / 'new.txt'
    with capture_owner(config, 'a', 'old', tmp_path):
        _atomic_write_bytes(str(source), b'first\n', existed_before=False)
    store = TurnFileStore(tmp_path / 'history', total_limit=1)
    first = store.list('a')[0]
    store.finish('a', 'different')
    assert store.read('a', first['id'])['state'] == 'available'
    store.finish('a', 'old')
    purged = store.read('a', first['id'])
    assert purged['state'] == 'purged' and purged['added'] == 1 and purged['patch'] == ''
    assert original.read_bytes() == b'unrelated attachment' and source.read_bytes() == b'first\n'
    # A later task can still capture fresh content using the normal budget.
    with capture_owner(config, 'a', 'new', tmp_path):
        _atomic_write_bytes(str(source), b'second\n', existed_before=True)
    assert len(store.list('a')) == 2
    assert store.list('a')[-1]['state'] == 'available'


def test_new_attachments_live_outside_projects_and_reject_other_conversations(tmp_path):
    import base64
    import pytest
    from pathlib import Path
    from core.desktop_workbench import DesktopFiles
    project = tmp_path / 'project'
    project.mkdir()
    config = SimpleNamespace(get_chat_history_dir=lambda: str(tmp_path / 'history'))
    files = DesktopFiles(SimpleNamespace(get_conversation_record=lambda cid: {'meta': {'workspace_dir': str(project)}}), config)
    pasted = files.dispatch('workspace.attach_data', {'conversation_id': 'a', 'name': '截图.png', 'base64': base64.b64encode(b'image').decode()})
    source = tmp_path / 'report.txt'
    source.write_bytes(b'report')
    uploaded = files.dispatch('workspace.attach', {'conversation_id': 'a', 'paths': [str(source)]})[0]
    assert list(project.iterdir()) == []
    for item in (pasted, uploaded):
        assert not Path(item['path']).is_relative_to(project)
        assert files.checked_file('a', item['path']) == Path(item['path'])
        assert files.preview_file('a', item['path']) == Path(item['path'])
        with pytest.raises(ValueError):
            files.checked_file('b', item['path'])
        with pytest.raises(ValueError, match='不属于'):
            files.preview_file('b', item['path'])
    legacy = project / 'attachments' / 'old.txt'
    legacy.parent.mkdir()
    legacy.write_bytes(b'old')
    assert files.preview_file('a', str(legacy)) == legacy


def test_file_history_failure_preserves_chat_and_committed_file(tmp_path, monkeypatch):
    from core.turn_files import TurnFileStore, capture_owner
    from core.filesystem_ops import _atomic_write_bytes
    from core.desktop_workbench import DesktopFiles
    config = SimpleNamespace(get_chat_history_dir=lambda: str(tmp_path / 'history'))
    source = tmp_path / 'result.txt'
    complete = TurnFileStore.complete
    monkeypatch.setattr(TurnFileStore, 'complete', Mock(side_effect=OSError('disk unavailable')))
    with capture_owner(config, 'chat', 'run', tmp_path):
        _atomic_write_bytes(str(source), b'committed once\n', existed_before=False)
    assert source.read_bytes() == b'committed once\n'
    monkeypatch.setattr(TurnFileStore, 'complete', complete)
    store = TurnFileStore(tmp_path / 'history')
    pending = store.list('chat')[0]
    assert pending['state'] == 'pending'
    assert store.read('chat', pending['id'])['patch'] == ''
    files = DesktopFiles(SimpleNamespace(get_conversation_record=lambda cid: {'meta': {'workspace_dir': str(tmp_path)}}), config)
    monkeypatch.setattr(files, 'history_store', Mock(side_effect=OSError('unreadable index')))
    message = {'id': 'answer', 'role': 'assistant', 'content': '已保存 result.txt'}
    displayed = files.message_files('chat', [message])[0]
    assert displayed['content'] == message['content']
    assert '聊天内容已保留' in displayed['file_history_error']
    assert 'file_history_error' not in message
