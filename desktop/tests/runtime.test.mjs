import assert from 'node:assert/strict';
import test from 'node:test';
import { displayMessages, mergeEvents, messageGroups, replyParts } from '../src/runtime.ts';

test('event cursor is scoped by run and duplicate batches are harmless', () => {
  const events = mergeEvents([{run_id:'a', sequence:1}], [{run_id:'b', sequence:1}, {run_id:'b', sequence:1}]);
  assert.equal(events.length, 2);
});

test('reply folding preserves stage text, tools, final output and user guidance order', () => {
  const messages = [
    {id:'u',role:'user',content:'整理资料'},
    {id:'a',role:'assistant',content:'先读取资料',reasoning_content:'逐项检查',tool_calls:[{id:'call',function:{name:'read_file'}}],meta:{turn_id:'r'}},
    {id:'t',role:'tool',tool_call_id:'call',content:'原文',meta:{turn_id:'r'}},
    {id:'g',role:'user',content:'保留来源',meta:{same_turn_guidance:true,turn_id:'r'}},
    {id:'b',role:'assistant',content:'整理完成',meta:{turn_id:'r'}},
    {id:'c',role:'assistant',content:'下一轮',meta:{turn_id:'r2'}}
  ];
  const groups=messageGroups(messages);
  assert.deepEqual(groups.map(group=>group.messages.map(message=>message.id)),[['u'],['a','t'],['g'],['b'],['c']]);
  const parts=replyParts([messages[1],messages[2],messages[4]]);
  assert.equal(parts.answer,messages[4]);
  assert.equal(parts.results.get('call'),messages[2]);
  assert.equal(parts.hasProcess,true);
  assert.equal(replyParts([messages[1],messages[2]]).answer,undefined);
  assert.equal(replyParts([messages[4]]).hasProcess,false);
});

test('attachment-only user messages stay visible after projection',()=>{
  const message={id:'attachment',role:'user',content:'',meta:{desktop_attachments:[{path:'a.pdf',name:'a.pdf'}]}};
  assert.deepEqual(displayMessages([message],[]),[message]);
});

test('reply folding retains an attachment-only answer and an empty tool result',()=>{
  const answer={id:'file',role:'assistant',content:'',meta:{desktop_attachments:[{path:'report.pdf',name:'report.pdf'}]}};
  const result={id:'tool',role:'tool',tool_call_id:'call',content:''};
  assert.equal(replyParts([answer]).answer,answer);
  const parts=replyParts([result,answer]);
  assert.equal(parts.answer,answer);
  assert.equal(parts.hasProcess,true);
  assert.equal(parts.results.get('call'),result);
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
