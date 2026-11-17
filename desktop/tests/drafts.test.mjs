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
    if (method.startsWith('draft.')) throw Error('input must stay local');
    if (method === 'conversation.send') {
      if (!receipts.has(params.request_id)) {
        if (params.draft_revision === remote.revision) remote = { value: { text: '', attachments: [] }, revision: remote.revision + 1 };
        receipts.set(params.request_id, { accepted: true, draft: structuredClone(remote) });
      }
      return receipts.get(params.request_id);
    }
    if (method === 'conversation.receipt') return receipts.get(params.request_id) || { accepted: false };
  };
  return { store: new Drafts(rpc, storage), storage, rpc, calls, setRemote: value => {remote=structuredClone(value);}, remote: () => structuredClone(remote) };
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
  assert.ok(f.store.get('a').pending);
  assert.equal(f.store.needsRecovery('a'), false, 'a normal slow submission is not an unresolved send');
  f.store.change('a', { text: '继续输入', attachments: [] });
  const saved = f.store.save('a');
  release();
  await Promise.all([sent, saved]);
  assert.equal(f.store.get('a').value.text, '继续输入');
  assert.equal(new Drafts(f.rpc, f.storage).get('a').value.text, '继续输入');
  assert.equal(f.calls.some(call => call.method.startsWith('draft.')), false);
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
  assert.equal(restored.needsRecovery('a'), true);
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

test('legacy conflicts cannot replace current input or require backend synchronization', async () => {
  const f = fixture();
  const value = {text:'当前输入', attachments:[{name:'附件.txt', path:'a.txt', size:2}]};
  f.storage.setItem(f.store.key('a'), JSON.stringify({value, revision:0, dirty:true, conflict:true, error:'旧版本冲突'}));
  const restored = new Drafts(f.rpc, f.storage);
  restored.get('a'); // React may read before the conversation snapshot arrives.
  for (let i=0;i<3;i++) {
    restored.restore('a', {value:{text:'后台旧内容', attachments:[]}, revision:9+i});
    await restored.save('a');
    assert.deepEqual(restored.get('a').value, value);
  }
  assert.equal(restored.get('a').conflict, undefined);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(new Drafts(f.rpc,f.storage).get('a').value, value);
});

test('legacy input imports once and stays cleared after restart', async () => {
  const f=fixture(),legacy={value:{text:'旧版未发送',attachments:[]},revision:7};
  f.store.get('a');f.store.restore('a',legacy);
  assert.equal(f.store.get('a').value.text,'旧版未发送');
  f.store.change('a',{text:'',attachments:[]});
  const restored=new Drafts(f.rpc,f.storage);restored.restore('a',legacy);
  assert.equal(restored.get('a').value.text,'');
  assert.equal(f.calls.length,0);
});

test('new chat carries input and attachments, while ordinary switching remains isolated', async () => {
  const f=fixture(),value={text:'继续编辑',attachments:[{name:'材料.txt',path:'a.txt',size:2}]};
  f.store.change('a',value);await f.store.carry('a','new');
  assert.deepEqual(f.store.get('new').value,value);
  assert.deepEqual(f.store.get('a').value,{text:'',attachments:[]});
  f.store.change('a',{text:'另一件事',attachments:[]});
  const restored=new Drafts(f.rpc,f.storage);
  assert.equal(restored.get('a').value.text,'另一件事');
  assert.deepEqual(restored.get('new').value,value);
  assert.equal(f.calls.length,0);
});

test('failed attachment carry preserves original input; late typing is not removed by a transfer', async () => {
  const f=fixture(),first={text:'新聊天内容',attachments:[{name:'材料',path:'a.txt',size:2}]};
  f.store.change('a',first);
  await assert.rejects(f.store.carry('a','new',async()=>{throw Error('copy failed');}),/copy failed/);
  assert.deepEqual(f.store.get('a').value,first);
  await f.store.carry('a','new',async input=>{
    f.store.change('a',{text:'转交期间继续输入',attachments:[]});
    return {...input,attachments:[{name:'材料',path:'new/a.txt',size:2}]};
  });
  assert.equal(f.store.get('a').value.text,'转交期间继续输入');
  assert.equal(f.store.get('new').value.attachments[0].path,'new/a.txt');
});

test('new chat never carries an in-flight submission, only later input', async () => {
  const f=fixture(),sent={text:'提交内容',attachments:[{name:'旧附件',path:'old.txt',size:2}]};
  f.store.change('a',sent);
  let release;const gate=new Promise(resolve=>release=resolve);
  f.store.rpc=async(method,p)=>{if(method==='conversation.send')await gate;return f.rpc(method,p);};
  const sending=f.store.submit('a',sent);await new Promise(resolve=>setImmediate(resolve));
  await f.store.carry('a','blank');assert.equal(f.store.get('blank').value.text,'');
  f.store.change('a',{text:'下一条内容',attachments:[...sent.attachments,{name:'新附件',path:'new.txt',size:3}]});
  await f.store.carry('a','new');
  assert.deepEqual(f.store.get('new').value,{text:'下一条内容',attachments:[{name:'新附件',path:'new.txt',size:3}]});
  release();await sending;
  assert.equal(f.store.get('a').value.text,'');assert.equal(f.store.get('new').value.text,'下一条内容');
  assert.equal(f.calls.filter(call=>call.method==='conversation.send').length,1);
});

test('history resubmission does not consume current composer input', async () => {
  const f=fixture();f.store.change('a',{text:'当前内容',attachments:[]});
  await f.store.submit('a',{text:'历史内容',attachments:[],consume_draft:false});
  assert.equal(f.store.get('a').value.text,'当前内容');
});

test('failed local storage preserves visible input and blocks transfer or sending', async () => {
  const f=fixture();let notified=0;f.store.subscribe('a',()=>notified++);
  f.storage.setItem=()=>{throw Error('disk full');};
  assert.throws(()=>f.store.change('a',{text:'不能丢',attachments:[]}),/disk full/);
  assert.equal(notified,1);assert.equal(f.store.get('a').value.text,'不能丢');
  await assert.rejects(f.store.carry('a','new'),/disk full/);
  assert.equal(f.store.get('a').value.text,'不能丢');
  await assert.rejects(f.store.submit('a',{text:'不能丢',attachments:[]}),/disk full/);
  assert.equal(f.calls.length,0);
  assert.equal(f.store.needsRecovery('a'),false,'a local write failure never becomes an uncertain send');
});
