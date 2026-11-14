import assert from 'node:assert/strict';
import test from 'node:test';
import { Drafts } from '../src/drafts.ts';

function fixture() {
  const journal = new Map();
  const storage = { getItem: key => journal.get(key), setItem: (key, value) => journal.set(key, value), removeItem: key => journal.delete(key) };
  let remote = { value: { text: '', attachments: [] }, revision: -1 };
  const receipts = new Map();
  const calls = [];
  const rpc = async (method, params) => {
    calls.push({ method, params });
    if (method === 'draft.save') {
      assert.equal(params.revision, remote.revision);
      remote = { value: params.value, revision: remote.revision + 1 };
      return { saved: true, revision: remote.revision };
    }
    if (method === 'conversation.send') {
      if (!receipts.has(params.request_id)) {
        if (params.draft_revision === remote.revision) remote = { value: { text: '', attachments: [] }, revision: remote.revision + 1 };
        receipts.set(params.request_id, { accepted: true, draft: structuredClone(remote) });
      }
      return receipts.get(params.request_id);
    }
    if (method === 'conversation.receipt') return receipts.get(params.request_id) || { accepted: false };
  };
  return { store: new Drafts(rpc, storage), storage, rpc, calls, remote: () => structuredClone(remote) };
}

test('a late send receipt keeps typing that started during the request', async () => {
  const f = fixture();
  f.store.restore('a', f.remote());
  f.store.change('a', { text: '第一条', attachments: [] });
  let release;
  const wait = new Promise(resolve => { release = resolve; });
  f.store.rpc = async (method, params) => { if (method === 'conversation.send') await wait; return f.rpc(method, params); };
  const sent = f.store.submit('a', { text: '第一条', attachments: [] });
  await new Promise(resolve => setImmediate(resolve));
  f.store.change('a', { text: '继续输入', attachments: [] });
  const saved = f.store.save('a');
  release();
  await Promise.all([sent, saved]);
  assert.equal(f.store.get('a').value.text, '继续输入');
  assert.equal(f.remote().value.text, '继续输入');
});

test('an uncertain accepted send recovers without a second submission after reload', async () => {
  const f = fixture();
  f.store.restore('a', f.remote()); f.store.change('a', { text: '已发送', attachments: [] });
  f.store.rpc = async (method, params) => {
    if (method === 'conversation.receipt') throw new Error('still disconnected');
    const result = await f.rpc(method, params); if (method === 'conversation.send') throw new Error('lost receipt'); return result;
  };
  await assert.rejects(f.store.submit('a', { text: '已发送', attachments: [] }));
  const restored = new Drafts(f.rpc, f.storage);
  restored.restore('a', f.remote());
  await restored.recoverSubmission('a');
  assert.equal(restored.get('a').value.text, '');
  assert.equal(restored.get('a').pending, undefined);
  assert.equal(f.calls.filter(call => call.method === 'conversation.send').length, 1);
});

test('a definitive rejection releases the command but preserves input', async () => {
  const f = fixture();
  f.store.restore('a', f.remote()); f.store.change('a', { text: '保留输入', attachments: [] });
  f.store.rpc = async (method, params) => {
    if (method === 'conversation.send') throw new Error('missing model');
    if (method === 'conversation.receipt') return { accepted: false, rejected: true };
    return f.rpc(method, params);
  };
  await assert.rejects(f.store.submit('a', { text: '保留输入', model_id: 'missing', attachments: [] }));
  assert.equal(f.store.get('a').pending, undefined);
  assert.equal(f.store.get('a').value.text, '保留输入');
  f.store.rpc = f.rpc;
  await f.store.submit('a', { text: '保留输入', model_id: 'configured', attachments: [] });
  assert.equal(f.store.get('a').value.text, '');
});

test('an immediately queryable receipt completes without resubmitting', async () => {
  const f = fixture();
  f.store.restore('a', f.remote()); f.store.change('a', { text: '一次发送', attachments: [] });
  f.store.rpc = async (method, params) => { const result = await f.rpc(method, params); if (method === 'conversation.send') throw new Error('lost reply'); return result; };
  const result = await f.store.submit('a', { text: '一次发送', attachments: [] });
  assert.equal(result.accepted, true);
  assert.equal(f.calls.filter(call => call.method === 'conversation.send').length, 1);
  assert.equal(f.store.get('a').pending, undefined);
});

test('a conflicting recovery never silently overwrites the saved draft', async () => {
  const f = fixture();
  f.store.restore('a', f.remote()); f.store.change('a', { text: '本机未保存', attachments: [] });
  const remote = { value: { text: '另一处保存', attachments: [] }, revision: 9 };
  const restored = new Drafts(f.rpc, f.storage);
  restored.restore('a', remote);
  assert.equal(restored.get('a').conflict, true);
  await assert.rejects(restored.save('a'), /草稿/);
  assert.equal(f.calls.length, 0);
  restored.resolve('a', false, remote);
  assert.equal(restored.get('a').value.text, '另一处保存');
});
