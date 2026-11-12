"""Isolated desktop/runtime benchmark with deterministic model and tool output.

This measures local coordination/persistence, not model or network throughput.
Run one process per case; --source permits comparison with a source overlay.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import threading
import time
import uuid
from unittest.mock import patch

from qa_support import prepare_qa_directory

QA = prepare_qa_directory(__file__)
ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--sessions', type=int, choices=(1, 8, 32), default=1)
parser.add_argument('--source', type=Path)
parser.add_argument('--slow-ui-ms', type=float, default=0)
parser.add_argument('--children', action='store_true')
args = parser.parse_args()
sys.path.insert(0, str(ROOT))
if args.source:
    sys.path.insert(0, str(args.source.resolve()))
directory = QA / (('b' if args.source else 'c') + '-' + uuid.uuid4().hex[:6])
directory.mkdir()
os.environ['COWORK_APP_DATA_DIR'] = str(directory)
os.environ['COWORK_EXTERNAL_BACKGROUND_SERVICES'] = '0'
os.environ['APPDATA'] = str(directory / 'appdata')
os.environ['LOCALAPPDATA'] = str(directory / 'localappdata')

from core.desktop_service import DesktopService
from core.agent_manager import get_agent_manager_registry

commits = [0]
connect = sqlite3.connect
def traced_connect(*a, **kw):
    db = connect(*a, **kw)
    def trace(sql):
        if sql.strip().upper() == 'COMMIT':
            commits[0] += 1
    db.set_trace_callback(trace)
    return db

class Provider:
    provider_name = 'OpenAI'
    model_name = 'fixture'
    base_url = 'https://example.invalid/v1'
    api_protocol = 'chat_completions'
    thinking_enabled = False
    requires_responses_replay = False
    requires_deepseek_responses_replay = False

    def chat_stream(self, messages, tools=None, **_):
        names = {item['function']['name'] for item in tools or []}
        if not any(item['role'] == 'tool' for item in messages) and 'text_file_read' in names:
            yield {'type': 'tool_call', 'id': 'probe', 'index': 0,
                   'function': {'name': 'text_file_read', 'arguments': '{"path":"probe.txt"}'}}
            return
        slow = any('STOP_PROBE' in str(item.get('content')) for item in messages if item['role'] == 'user')
        for _ in range(5000 if slow else 256):
            yield {'type': 'content', 'content': '可重复的本地输出。' * 8}
            time.sleep(.002)

first, starts = {}, {}
def publish(event):
    if event.get('event') == 'runtime' and event.get('data', {}).get('type') == 'content':
        first.setdefault(event['conversation_id'], time.perf_counter())
    if args.slow_ui_ms:
        time.sleep(args.slow_ui_ms / 1000)

def distribution(values):
    values = sorted(values)
    return {'median_ms': round(statistics.median(values) * 1000, 2),
            'max_ms': round(max(values) * 1000, 2)}

with patch('sqlite3.connect', traced_connect), patch('core.llm.factory.LLMFactory.create_provider', side_effect=lambda *_a, **_k: Provider()):
    service = DesktopService(publish)
    model = service.dispatch('settings.save_model', {'name': 'Benchmark', 'model_name': 'fixture',
                        'base_url': Provider.base_url, 'api_key': 'isolated-fixture'})['selected_model_id']
    conversations = [service.dispatch('conversation.create', {}) for _ in range(args.sessions)]
    for conversation in conversations:
        (Path(conversation['meta']['workspace_dir']) / 'probe.txt').write_text('fixture input', encoding='utf-8')
    commit_start = commits[0]
    high_water = {'pending_records': 0, 'pending_bytes': 0, 'backlog_bytes': 0}
    sampling = threading.Event()
    def sample():
        while not sampling.wait(.005):
            ledger = getattr(service, 'ledger', None)
            if ledger:
                for key, attr in [('pending_records', 'pending_count'), ('pending_bytes', 'pending_bytes'), ('backlog_bytes', 'backlog_bytes')]:
                    high_water[key] = max(high_water[key], getattr(ledger, attr))
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    def submit(conversation, text='BENCHMARK'):
        cid = conversation['id']
        starts[cid] = time.perf_counter()
        service.dispatch('conversation.send', {'conversation_id': cid, 'request_id': uuid.uuid4().hex,
            'message_id': uuid.uuid4().hex, 'text': text, 'model_id': model})
        return time.perf_counter() - starts[cid]
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.sessions) as executor:
        receipts = list(executor.map(submit, conversations))
    children = []
    if args.children:
        for conversation in conversations:
            manager = get_agent_manager_registry().get_session_manager(conversation['id'], service.storage, service.config,
                                  workspace_dir=conversation['meta']['workspace_dir'])
            children.append((manager, manager.spawn_agent('CHILD_BENCHMARK')['agent_id']))
    deadline = time.monotonic() + 180
    while True:
        runs = [service.store.latest_run(c['id']) for c in conversations]
        if all(run['status'] in {'completed', 'failed', 'interrupted'} for run in runs) and all(
                not manager._agents[aid].status in {'running', 'queued', 'stopping'} for manager, aid in children):
            break
        if time.monotonic() > deadline:
            raise TimeoutError('benchmark did not finish')
        time.sleep(.02)
    elapsed = time.perf_counter() - start
    assert all(run['status'] == 'completed' for run in runs), runs
    children_status = [manager._agents[aid].status for manager, aid in children]
    assert all(status == 'completed' for status in children_status), children_status
    ledger = getattr(service, 'ledger', None)
    if ledger:
        ledger.barrier()
    workload_commits = commits[0] - commit_start
    result = {'source': 'baseline' if args.source else 'current', 'sessions': args.sessions, 'children': len(children),
              'slow_ui_ms': args.slow_ui_ms, 'elapsed_s': round(elapsed, 3), 'receipts': distribution(receipts),
              'first_output': distribution([first[c['id']] - starts[c['id']] for c in conversations]),
              'sqlite_commits': workload_commits, 'high_water': high_water,
              'ledger': dict(ledger.metrics) if ledger else None}
    target = conversations[0]
    first.pop(target['id'], None)
    submit(target, 'STOP_PROBE')
    while target['id'] not in first:
        if time.monotonic() > deadline:
            raise TimeoutError('stop probe did not start')
        time.sleep(.005)
    run = service.store.latest_run(target['id'])
    stopped = time.perf_counter()
    service.dispatch('conversation.stop', {'conversation_id': target['id'], 'run_id': run['id']})
    result['stop_ack_ms'] = round((time.perf_counter() - stopped) * 1000, 2)
    while service.streams:
        time.sleep(.01)
    result['stop_complete_ms'] = round((time.perf_counter() - stopped) * 1000, 2)
    history = service.dispatch('conversation.get', {'conversation_id': target['id']})
    assert history['run']['status'] == 'interrupted'
    history_path = Path(service.config.get_chat_history_dir())
    result['runtime_log_bytes'] = sum(p.stat().st_size for directory_name in ('runtime_wal', 'runtime_journal_v2') for p in (history_path / directory_name).rglob('*') if p.is_file())
    sampling.set(); sampler.join()
    service.dispatch('app.shutdown', {})
    service.server.shutdown(); service.server.server_close(); service.state.skill_catalog.stop_watching()
    restarted = time.perf_counter()
    os.environ['COWORK_APP_DATA_DIR'] = str(directory)
    restored = DesktopService(lambda _event: None)
    after = restored.dispatch('conversation.get', {'conversation_id': target['id']})
    result['restart_and_read_ms'] = round((time.perf_counter() - restarted) * 1000, 2)
    assert after['messages'] == history['messages']
    assert after['run']['status'] == 'interrupted'
    restored.dispatch('app.shutdown', {})
    restored.server.shutdown(); restored.server.server_close(); restored.state.skill_catalog.stop_watching()
    result['data_dir'] = str(directory)
    (directory / 'metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
