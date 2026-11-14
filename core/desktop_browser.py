"""One-shot, cancellable requests to the sandboxed Electron browser host."""
import threading
import time
import uuid
from urllib.parse import urlsplit

from core.workbench import WorkbenchError


class DesktopBrowser:
    methods = {"browser.open", "browser.navigate", "browser.close", "browser.command", "browser.host_check", "browser.host_result", "browser.host_state"}

    def __init__(self, workbench):
        self.w = workbench
        self.pending = {}
        self.lock = threading.RLock()

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
                if method == 'browser.host_result':
                    request['result'] = p['result']
                    request['event'].set()
                    return {'ok': True}
            self.check(request)
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
            if action == 'command' and arguments.get('command') not in {'info', 'back', 'forward', 'reload', 'find', 'stop_find', 'zoom'}:
                raise ValueError('不支持的网页操作。')
            if action == 'navigate':
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
            if result.get('ok') and action == 'close':
                with self.w.lock:
                    self.w.close_tab(cid, tab.id)
            return {**result, **({'tab': tab.snapshot()} if action == 'open' else {})}
        finally:
            with self.lock:
                self.pending.pop(key, None)
