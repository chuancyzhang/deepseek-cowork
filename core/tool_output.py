"""Bounded process capture; durable output lives outside conversation history."""
from __future__ import annotations

import codecs
from contextlib import contextmanager
import hashlib
import json
import logging
import locale
import os
from pathlib import Path
import subprocess
import threading
import time

from core.process_utils import terminate_process_tree
from core.platform_runtime import platform_runtime

log = logging.getLogger(__name__)
PREVIEW_BYTES = 8192
RESULT_BYTES = 16384
PAGE_BYTES = 65536
LOG_BYTES = 50 * 1024 * 1024


class ProcessExecutionError(RuntimeError):
    def __init__(self, result):
        self.result = result
        super().__init__(result.get('error') or result.get('message') or result.get('stderr') or f"进程退出码 {result.get('exit_code')}")


def bounded(text, limit):
    return str(text).encode('utf-8')[-limit:].decode('utf-8', errors='ignore')


def output_base(root, session_id, run_id, call_id):
    # Never use renderer/model supplied identifiers as path components.
    session = hashlib.sha256(str(session_id).encode()).hexdigest()[:24]
    execution = hashlib.sha256(json.dumps([run_id, call_id], ensure_ascii=False).encode()).hexdigest()
    return Path(root) / 'tool_output' / session / execution


@contextmanager
def metadata_lock(base):
    # Readers close the metadata handle before publication (Windows sharing rules).
    with platform_runtime.filesystem_path(base.with_suffix('.lock')).open('a+b') as handle:
        deadline = time.monotonic() + 5
        while True:
            try:
                handle.seek(0)
                platform_runtime.lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('日志状态暂时无法保存或读取。')
                time.sleep(.01)
        # Windows permits locking beyond EOF. Reading or initializing the byte
        # before acquisition races another reader/writer's mandatory byte lock.
        yield


class OutputLog:
    def __init__(self, root, session_id, run_id, task_id, call_id, limit=None):
        self.base = output_base(root, session_id, run_id, call_id)
        platform_runtime.filesystem_path(self.base.parent).mkdir(parents=True, exist_ok=True)
        self.path = self.base.with_suffix('.jsonl')
        self.handle = platform_runtime.filesystem_path(self.path).open('xb')
        self.lock = threading.RLock()
        self.limit = max(0, int(limit if limit is not None else os.environ.get('COWORK_TOOL_LOG_BYTES', LOG_BYTES)))
        self.last_save = 0.0
        self.saved_state = None
        self.meta = dict(session_id=session_id, run_id=run_id, task_id=task_id, tool_call_id=call_id,
                         path=str(self.path), bytes=0, sequence=0, preview='', incomplete=False,
                         status='running', started_at=time.time())
        self.checkpoint()

    def checkpoint(self, force=False):
        with self.lock:
            state = tuple(self.meta.get(key) for key in ('sequence', 'status', 'incomplete', 'reason', 'finished_at'))
            if state == self.saved_state:
                return
            if not force and time.monotonic() - self.last_save < .2:
                return
            self.handle.flush()
            os.fsync(self.handle.fileno())
            target = self.base.with_suffix('.json')
            temporary = self.base.with_suffix('.tmp')
            with platform_runtime.filesystem_path(temporary).open('w', encoding='utf-8') as stream:
                json.dump(self.meta, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            with metadata_lock(self.base):
                platform_runtime.replace(temporary, target)
            self.last_save = time.monotonic()
            self.saved_state = state

    def append(self, channel, text):
        with self.lock:
            if not text or self.meta['incomplete']:
                return
            record = (json.dumps({'sequence': self.meta['sequence'] + 1, 'channel': channel, 'text': text}, ensure_ascii=False) + '\n').encode('utf-8')
            if self.meta['bytes'] + len(record) > self.limit:
                self.meta.update(incomplete=True, reason='日志已达上限，后续输出已省略。')
                log.info('tool.output_limit run=%s task=%s bytes=%s', self.meta['run_id'], self.meta['task_id'], self.meta['bytes'])
                self.checkpoint(force=True)
                return
            self.handle.write(record)
            self.meta['bytes'] += len(record)
            self.meta['sequence'] += 1
            lines = (self.meta['preview'] + text).replace('\r\n', '\n').replace('\r', '\n').split('\n')
            # A final separator ends the last line; keep it for the next chunk
            # without charging its empty split item against the eight-line tail.
            limit = 9 if lines[-1] == '' else 8
            self.meta['preview'] = bounded('\n'.join(lines[-limit:]), PREVIEW_BYTES)
            self.checkpoint()

    def finish(self, status):
        with self.lock:
            if self.handle.closed:
                return
            self.meta.update(status=status, finished_at=time.time())
            try:
                self.checkpoint(force=True)
            finally:
                self.handle.close()


def current_output():
    from core.tool_tasks import current_tool_run, current_tool_task
    owner, task = current_tool_run(), current_tool_task()
    if owner is None or task is None or not owner.output_root:
        return None
    if task.output is None:
        task.output = OutputLog(owner.output_root, owner.session_id, owner.run_id, task.task_id, task.call_id)
    return task.output


def compact_result(result):
    """Archive oversized ordinary results, keeping control/discovery protocols intact."""
    if isinstance(result, dict) and any(key in result for key in
            ('tools', 'skills', 'content_parts', 'interaction_request', 'interaction_response', 'background_tool')):
        return result
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    if len(text.encode('utf-8')) <= 32768:
        return result
    from core.tool_tasks import current_tool_task
    existing_output = getattr(current_tool_task(), 'output', None)
    output = current_output()
    if output is None:
        return result
    if existing_output is None:
        for start in range(0, len(text), 2048):
            output.append('result', text[start:start + 2048])
    output.checkpoint(force=True)
    summary = {key: bounded(value, 2048) if isinstance(value, str) else value
               for key, value in (result.items() if isinstance(result, dict) else [])
               if isinstance(value, (str, int, float, bool)) and key not in {'content', 'stdout', 'stderr'}}
    return {**summary, 'content': bounded(text, RESULT_BYTES), 'result_truncated': True,
            'output_ref': str(output.path),
            'output_note': '返回值较长，正文已独立保存。可使用 text_file_read 按 next_offset 分段读取日志。',
            'output_incomplete': output.meta['incomplete']}


def read_owned_output(path, offset=1, limit=None):
    """Allow the existing text reader to page a run-owned log without loading it all."""
    from core.tool_tasks import current_tool_run
    owner = current_tool_run()
    if owner is None or not owner.output_root:
        return None
    candidate = Path(path)
    parent = output_base(owner.output_root, owner.session_id, '', '').parent
    if candidate.suffix != '.jsonl' or candidate.parent.resolve() != parent.resolve():
        return None
    with metadata_lock(candidate.with_suffix('')):
        with platform_runtime.filesystem_path(candidate.with_suffix('.json')).open(encoding='utf-8') as stream:
            meta = json.load(stream)
    expected = output_base(owner.output_root, owner.session_id, meta['run_id'], meta['tool_call_id']).with_suffix('.jsonl')
    if meta['session_id'] != owner.session_id or candidate.resolve() != expected.resolve():
        raise ValueError('日志不属于当前对话。')
    offset, limit = max(1, int(offset)), max(1, min(int(limit or 100), 100))
    lines, size = [], 0
    with platform_runtime.filesystem_path(candidate).open('rb') as stream:
        for index, raw in enumerate(stream, 1):
            if stream.tell() > meta['bytes']:
                break
            if index < offset:
                continue
            if len(lines) >= limit or size + len(raw) > PAGE_BYTES:
                break
            lines.append(raw.decode('utf-8'))
            size += len(raw)
    next_offset = offset + len(lines)
    more = next_offset <= meta['sequence']
    return {'ok': True, 'path': str(candidate), 'content': ''.join(lines), 'start_line': offset,
            'returned_lines': len(lines), 'total_lines': meta['sequence'], 'next_offset': next_offset if more else None,
            'truncated': more, 'output_incomplete': meta['incomplete'], 'encoding': 'utf-8'}


def read_output(root, session_id, run_id, call_id, *, cursor=None, limit=PAGE_BYTES):
    base = output_base(root, session_id, run_id, call_id)
    if not platform_runtime.filesystem_path(base.with_suffix('.json')).exists():
        return None
    with metadata_lock(base):
        with platform_runtime.filesystem_path(base.with_suffix('.json')).open(encoding='utf-8') as stream:
            meta = json.load(stream)
    if cursor is None:
        return meta
    cursor = max(0, min(int(cursor), meta['bytes']))
    size = max(1, min(int(limit), PAGE_BYTES))
    records = []
    with platform_runtime.filesystem_path(base.with_suffix('.jsonl')).open('rb') as stream:
        stream.seek(cursor)
        end = min(meta['bytes'], cursor + size)
        while stream.tell() < end:
            raw = stream.readline(PAGE_BYTES)
            if not raw or not raw.endswith(b'\n'):
                raise ValueError('日志读取位置无效，请重新读取。')
            if stream.tell() > end:
                stream.seek(-len(raw), 1)
                break
            records.append(json.loads(raw))
        next_cursor = stream.tell()
    return {**meta, 'records': records, 'cursor': next_cursor, 'has_more': next_cursor < meta['bytes']}


def capture_process(process, context=None, *, input_bytes=None, timeout=None, output=None):
    """Two bounded readers apply pipe backpressure; cancellation never takes their lock."""
    from core.tool_cancellation import init_abort_state
    from core.tool_tasks import current_tool_run, current_tool_task
    state = init_abort_state(context)
    owner, task = current_tool_run(), current_tool_task()
    try:
        output = output if output is not None else current_output()
    except Exception:
        terminate_process_tree(process)
        for pipe in (process.stdout, process.stderr, process.stdin):
            if pipe:
                pipe.close()
        raise
    tails = {'stdout': '', 'stderr': ''}
    totals = {'stdout': 0, 'stderr': 0}
    failures = []
    deadline = time.monotonic() + timeout if timeout else None
    threads = []
    flush_stop = threading.Event()

    def flush():
        while not flush_stop.wait(.2):
            try:
                output.checkpoint()
            except Exception as exc:
                failures.append(exc)
                return

    flusher = threading.Thread(target=flush, daemon=True) if output else None
    if flusher:
        flusher.start()

    def read(channel, pipe):
        decoder = codecs.getincrementaldecoder('utf-8-sig')(errors='strict')
        try:
            while True:
                raw = pipe.read1(4096) if hasattr(pipe, 'read1') else pipe.read(4096)
                if isinstance(raw, bytes):
                    pending = decoder.getstate()[0]
                    try:
                        text = decoder.decode(raw, final=not raw)
                    except UnicodeDecodeError:
                        decoder = codecs.getincrementaldecoder(locale.getencoding())(errors='replace')
                        text = decoder.decode(pending + raw, final=not raw)
                else:
                    text = raw or ''
                if text:
                    totals[channel] += len(text.encode('utf-8'))
                    tails[channel] = bounded(tails[channel] + text, RESULT_BYTES)
                    if output:
                        output.append(channel, text)
                if not raw:
                    break
        except Exception as exc:
            failures.append(exc)
        finally:
            pipe.close()

    for channel in tails:
        pipe = getattr(process, channel)
        if pipe is not None:
            thread = threading.Thread(target=read, args=(channel, pipe), daemon=True)
            thread.start()
            threads.append(thread)
    if process.stdin:
        def write_input():
            try:
                if input_bytes:
                    process.stdin.write(input_bytes)
                    process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()
        threading.Thread(target=write_input, daemon=True).start()
    cancelled = timed_out = False
    stopped = True
    while process.poll() is None or any(thread.is_alive() for thread in threads):
        cancelled = state['aborted'] or bool(owner and task and owner.abort_requested(task.task_id))
        timed_out = bool(deadline and time.monotonic() >= deadline)
        if cancelled or timed_out or failures:
            stopped = terminate_process_tree(process)
            for thread in threads:
                thread.join(2)
            break
        time.sleep(.025)
    flush_stop.set()
    if flusher:
        flusher.join(2)
    if output:
        if failures:
            output.meta.update(incomplete=True, reason='日志保存失败，已请求停止执行。')
        try:
            output.checkpoint(force=True)
        except Exception as exc:
            failures.append(exc)
            stopped = terminate_process_tree(process)
    result = dict(tails, ok=process.poll() == 0, exit_code=process.poll())
    if output:
        result['output_ref'] = str(output.path)
    if any(totals[channel] > RESULT_BYTES for channel in tails):
        result['output_truncated'] = True
    if failures:
        log.error('tool.output_failed pid=%s reason=%s', process.pid, type(failures[0]).__name__)
        result.update(ok=False, status='failed', error='日志保存失败，执行已停止。' if stopped else '日志保存失败，进程停止尚未确认。')
    elif cancelled:
        result.update(ok=False, aborted=True, status='cancelled' if stopped else 'unknown', error='工具执行已停止，已保留已有输出和文件。' if stopped else '已请求停止，进程结束尚未确认。')
    elif timed_out:
        result.update(ok=False, status='failed' if stopped else 'unknown', error=f'执行超过 {timeout:g} 秒，已请求停止。')
    return result


def process_text(result, stderr_label='STDERR:'):
    if result.get('aborted') or result.get('error'):
        return result
    text = result['stdout']
    if result['stderr']:
        text += ('\n' if text else '') + stderr_label + '\n' + result['stderr']
    if result.get('output_truncated'):
        text += '\n输出已省略；已保存日志：' + result.get('output_ref', '未配置日志存储')
    if result['exit_code']:
        return {'ok': False, 'status': 'failed', 'exit_code': result['exit_code'], 'error': f"进程退出码 {result['exit_code']}", 'content': text, 'output_ref': result.get('output_ref')}
    return text if text.strip() else '(No output)'
