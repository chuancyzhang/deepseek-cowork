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
