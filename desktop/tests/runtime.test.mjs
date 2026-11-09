import assert from 'node:assert/strict';
import test from 'node:test';
import { displayMessages, mergeEvents } from '../src/runtime.ts';

test('event cursor is scoped by run and duplicate batches are harmless', () => {
  const events = mergeEvents([{run_id:'a', sequence:1}], [{run_id:'b', sequence:1}, {run_id:'b', sequence:1}]);
  assert.equal(events.length, 2);
});
test('interrupted output stays before the next turn, without a false running state', () => {
  const messages = displayMessages([
    {id:'u1', role:'user', content:'one', meta:{turn_id:'a'}},
    {id:'u2', role:'user', content:'two', meta:{turn_id:'b'}},
    {id:'m2', role:'assistant', content:'saved two', meta:{turn_id:'b'}}
  ], [{type:'content', run_id:'a', source_message_id:'m1', delta:'partial'},
      {type:'content', run_id:'b', source_message_id:'m2', delta:'saved two'}]);
  assert.deepEqual(messages.map(m => m.id), ['u1', 'm1', 'u2', 'm2']);
  assert.equal(messages[1].live, false);
});

test('tool results survive streaming and reconcile once canonical messages arrive', () => {
  const events = [{type:'tool_call', run_id:'a', source_message_id:'m1', data:{id:'c1', name:'read_file'}},
    {type:'tool_result', run_id:'a', source_message_id:'m1', data:{id:'c1', name:'read_file', result:'file content'}},
    {type:'content', run_id:'a', source_message_id:'m2', delta:'done'}];
  const streamed = displayMessages([{id:'u1',role:'user',content:'read',meta:{turn_id:'a'}}], events, 'a');
  assert.equal(streamed.filter(m=>m.role==='tool').length,1);
  assert.equal(streamed.filter(m=>m.live).length,1);
  assert.equal(streamed.find(m=>m.live).id,'m2');
  const restored = displayMessages([{id:'u1',role:'user',content:'read',meta:{turn_id:'a'}},
    {id:'m1',role:'assistant',tool_calls:[{id:'c1',name:'read_file'}],meta:{turn_id:'a'}},
    {id:'t1',role:'tool',content:'file content',tool_call_id:'c1',meta:{turn_id:'a'}},
    {id:'m2',role:'assistant',content:'done',meta:{turn_id:'a'}}],events);
  assert.deepEqual(restored.map(m=>m.id),['u1','m1','t1','m2']);
});
