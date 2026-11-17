"""One-shot, cancellable requests to the sandboxed Electron browser host."""
import threading
import time
import uuid
import logging
from urllib.parse import urlsplit

from core.workbench import WorkbenchError
log = logging.getLogger("desktop.browser")


class DesktopBrowser:
    methods = {"browser.open", "browser.navigate", "browser.close", "browser.command", "browser.host_check", "browser.host_result", "browser.host_state", "browser.host_dispatch", "browser.host_receipt", "browser.host_popup", "browser.host_closed"}

    def __init__(self, workbench):
        self.w = workbench
        self.pending = {}
        self.lock = threading.RLock()
        self.tab_locks = {}

    def unresolved(self, cid):
        return self.w.s.ledger.record(f"browser/{cid}/unresolved", {})

    def record_receipt(self, request, value):
        cid = request['cid']
        receipt = {**(request.get('receipt') or {}), **value, 'updated_at': time.time()}
        request['receipt'] = receipt
        pending = receipt.get('effect_state') in {'pending', 'unknown'}
        operations = [{'kind': 'record', 'key': f"browser/{cid}/actions/{receipt['receipt_id']}", 'value': receipt}]
        current = self.unresolved(cid)
        if pending or current.get('receipt_id') == receipt['receipt_id']:
            operations.append({'kind': 'record', 'key': f"browser/{cid}/unresolved", 'value': receipt if pending else {}})
        self.w.s.ledger.append(cid, operations)
        log.info('browser.receipt session=%s run=%s tab=%s receipt=%s effect=%s', cid, receipt.get('run_id'), request['tab_id'], receipt['receipt_id'], receipt.get('effect_state'))
        return receipt

    @staticmethod
    def url(value):
        value = str(value or '').strip()
        if '://' not in value:
            value = 'https://' + value
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('请输入有效的 HTTP(S) 网址。')
        return value

    def dispatch(self, method, p):
        if method.startswith('browser.host_'):
            if method == 'browser.host_closed':
                cid = p['conversation_id']
                with self.w.lock:
                    try:
                        tab = self.w.store.get(cid, p['tab_id'])
                    except WorkbenchError:
                        return {'ok': True}
                    if tab.kind != 'web':
                        raise ValueError('关闭来源不是网页。')
                    self.w.store.close(cid, tab.id)
                self.w.changed(cid)
                return {'ok': True}
            if method == 'browser.host_popup':
                cid = p['conversation_id']
                with self.w.lock:
                    parent = self.w.store.get(cid, p['tab_id'])
                    if parent.kind != 'web':
                        raise ValueError('弹出页面的来源无效。')
                    url = 'about:blank' if p['url'] == 'about:blank' else self.url(p['url'])
                    tab = self.w.store.open(cid, 'web', title='新网页', url=url, status='loading')
                    self.w.store.set_owner(cid, tab.id, parent.owner)
                self.w.changed(cid)
                return {'tab': tab.snapshot()}
            if method == 'browser.host_state':
                with self.w.lock:
                    tab = self.w.store.get(p['conversation_id'], p['tab_id'])
                    if tab.kind != 'web':
                        raise ValueError('目标不是网页。')
                    tab.title = str(p.get('title') or tab.title)[:200]
                    tab.url = str(p.get('url') or tab.url)
                    tab.status = str(p.get('status') or tab.status)
                self.w.changed(tab.session_id)
                return {'ok': True}
            with self.lock:
                request = self.pending.get(p.get('id'))
                if not request or request['deadline'] < time.monotonic():
                    raise WorkbenchError('cancelled', '网页请求已结束，不会重复执行。')
                if method == 'browser.host_receipt':
                    if not request.get('receipt'):
                        raise WorkbenchError('invalid_receipt', '动作尚未登记，不能提交回执。')
                    value = p['result']
                    if value.get('receipt_id') and value['receipt_id'] != request['receipt']['receipt_id']:
                        raise WorkbenchError('invalid_receipt', '动作回执不属于此请求。')
                    # Persist only operational facts, never page/form contents.
                    receipt = self.record_receipt(request, {key: value[key] for key in ('status', 'effect_state', 'executed', 'error') if key in value})
                    return {'ok': True, 'receipt_id': receipt['receipt_id']}
                if method == 'browser.host_result':
                    request['result'] = p['result']
                    request['event'].set()
                    return {'ok': True}
            self.check(request)
            if method == 'browser.host_dispatch':
                with self.lock:
                    blocked = self.unresolved(request['cid'])
                    if blocked:
                        raise WorkbenchError('unknown', '前一浏览器动作结果尚未核实，请先读取现场并明确核对回执。')
                    identity = request.get('identity') or {}
                    receipt = self.record_receipt(request, {'receipt_id': f"{p['id']}:{p.get('step', 0)}", 'run_id': identity.get('run_id', ''),
                        'tab_id': request['tab_id'], 'action': p.get('action'), 'effect_state': 'pending', 'status': 'dispatching'})
                    return {'ok': True, 'receipt_id': receipt['receipt_id']}
            return {'ok': True}
        cid = p.get('conversation_id')
        if not self.w.s.storage.get_conversation_record(cid):
            raise ValueError('对话不存在。')
        return self.execute(cid, method.split('.')[1], p)

    def check(self, request):
        with self.w.lock:
            tab = self.w.store.get(request['cid'], request['tab_id'])
            if tab.control_revision != request['revision']:
                raise WorkbenchError('control_changed', '控制权已变化，本次操作未执行，请重新观察。')
            identity = request.get('identity')
            if identity:
                self.w.store.check_control(request['cid'], request['tab_id'], request['revision'])
                if self.w.s.request_agent({**identity, 'action': 'workbench_active'}).get('status') != 'ok':
                    raise WorkbenchError('cancelled', '原任务已取消，本次网页操作未执行。')

    def execute(self, cid, action, args, identity=None):
        with self.lock:
            serial = self.tab_locks.setdefault((cid, args.get('tab_id') or '__open__'), threading.RLock())
        with serial:
            return self._execute(cid, action, args, identity)

    def _execute(self, cid, action, args, identity=None):
        with self.w.lock:
            if action == 'open':
                local_path = ''
                if args.get('path'):
                    if args.get('url'):
                        raise ValueError('打开网页时请选择网址或本地 HTML 文件。')
                    path = self.w.s.checked_file(cid, args['path'])
                    if not path.is_file() or path.suffix.lower() not in {'.html', '.htm'}:
                        raise ValueError('本地网页必须是存在的 HTML 文件。')
                    local_path = str(path)
                tab = self.w.store.open(cid, 'web', activate=not identity, title='网页', path=local_path, url='' if local_path else self.url(args.get('url')), status='loading')
                self.w.store.set_owner(cid, tab.id, 'agent' if identity else 'human')
            else:
                tab = self.w.store.get(cid, args.get('tab_id'))
                if tab.kind != 'web':
                    raise WorkbenchError('invalid_kind', '目标不是网页。')
                if identity:
                    self.w.store.check_control(cid, tab.id)
                    self.w.store.set_owner(cid, tab.id, 'agent')
                elif tab.owner != 'human' and action != 'close' and not (action == 'command' and args.get('command') == 'info'):
                    raise WorkbenchError('human_control', '请先接管网页。')
            arguments = dict(args)
            if action == 'browser_action' and args.get('action') == 'resolve_unknown':
                if identity and self.w.s.request_agent({**identity, 'action': 'workbench_active'}).get('status') != 'ok':
                    raise WorkbenchError('cancelled', '任务已取消，回执未修改。')
                record = self.unresolved(cid)
                if not record or args.get('receipt_id') != record.get('receipt_id'):
                    raise WorkbenchError('invalid_receipt', '没有匹配的待核实动作。')
                if args.get('resolution') not in {'completed', 'not_executed', 'abandoned'} or not str(args.get('evidence') or '').strip():
                    raise ValueError('请提供现场核对依据和 completed、not_executed 或 abandoned 结论。')
                request = {'cid': cid, 'tab_id': tab.id, 'receipt': record}
                receipt = self.record_receipt(request, {'effect_state': args['resolution'], 'status': 'reconciled', 'evidence': str(args['evidence'])[:1000]})
                return {'ok': True, 'status': 'reconciled', 'receipt': receipt}
            if action == 'browser_file':
                if args.get('operation') == 'upload':
                    files = args.get('files')
                    if not isinstance(files, list) or not 1 <= len(files) <= 20:
                        raise ValueError('请选择 1–20 个工作区文件。')
                    paths = [self.w.s.checked_file(cid, value) for value in files]
                    if any(not path.is_file() for path in paths):
                        raise ValueError('上传文件不存在。')
                    arguments['files'] = [str(path) for path in paths]
                elif args.get('operation') == 'download':
                    target = self.w.s.checked_file(cid, args.get('path') or '')
                    if target.exists():
                        raise ValueError('下载目标已存在，请选择新路径。')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    arguments['path'] = str(target)
                else:
                    raise ValueError('不支持的文件操作。')
            if action == 'command' and arguments.get('command') not in {'info', 'back', 'forward', 'reload', 'find', 'stop_find', 'zoom'}:
                raise ValueError('不支持的网页操作。')
            if action == 'navigate':
                navigation = args.get('navigation', 'url')
                if navigation not in {'url', 'back', 'forward', 'reload'}:
                    raise ValueError('不支持的导航操作。')
                if navigation == 'url':
                    arguments['url'] = tab.url if tab.path and args.get('url') == tab.url else self.url(args.get('url'))
            key = uuid.uuid4().hex
            request = {'event': threading.Event(), 'cid': cid, 'tab_id': tab.id, 'revision': tab.control_revision,
                       'identity': identity, 'deadline': time.monotonic() + 25, 'result': None}
            with self.lock:
                self.pending[key] = request
            self.w.changed(cid)
            self.w.s.publish({'event': 'browser_host', 'data': {'id': key, 'conversation_id': cid, 'action': action, 'tab': tab.snapshot(), 'arguments': arguments}})
        try:
            while not request['event'].wait(.15):
                if time.monotonic() >= request['deadline']:
                    raise WorkbenchError('host_unavailable', '浏览器界面未响应。请打开桌面界面并检查实际页面后继续，操作不会自动重试。')
                self.check(request)
            result = request['result']
            blocked = self.unresolved(cid)
            if blocked:
                result['unresolved_action'] = blocked
            if result.get('ok') and action == 'close':
                with self.w.lock:
                    self.w.close_tab(cid, tab.id)
            return {**result, **({'tab': tab.snapshot()} if action == 'open' else {})}
        except Exception as error:
            if request.get('receipt') and request['receipt'].get('effect_state') in {'pending', 'unknown'}:
                receipt = self.record_receipt(request, {'effect_state': 'unknown', 'status': 'unknown', 'error': str(error)})
                return {'ok': False, 'status': 'unknown', 'effect_state': 'unknown', 'error': str(error), 'receipt': receipt, 'executed': True}
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)
