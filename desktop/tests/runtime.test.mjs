import assert from 'node:assert/strict';
import test from 'node:test';
import { displayMessages, mergeEvents, messageGroups, replyParts, MessageProjection, unwrapEvents, messageTime, turnTimings, elapsedTime, thinkingTime, interactionRecords } from '../src/runtime.ts';

test('run and reasoning durations use recorded boundaries and stay isolated by run', () => {
  const event = (type, created_at, source_message_id='a', run_id='r') => ({type,created_at,source_message_id,run_id});
  const events = [event('turn_started',100),event('thinking',102),event('thinking',103),event('log',104),
    event('content',105),event('tool_call',106),event('thinking',108,'b'),event('content',110,'b'),event('final',112,'b'),
    event('turn_started',200,'c','other'),event('thinking',202,'c','other')];
  const turns = turnTimings(events), done = turns.get('r'), live = turns.get('other');
  assert.equal(elapsedTime(done.start,done.end),12);
  assert.equal(thinkingTime(done,'a'),3);
  assert.equal(thinkingTime(done,'b'),2);
  assert.equal(thinkingTime(live,'c'),undefined);
  assert.equal(thinkingTime(live,'c',205),3);
  assert.equal(elapsedTime(live.start,live.end),undefined);
  assert.equal(elapsedTime(100,99),undefined);
  assert.equal(turnTimings([{type:'turn_started',run_id:'old'}]).size,0);
});

test('message time survives live deltas and journal replay, with saved time authoritative', () => {
  const first = {type:'thinking', run_id:'r', source_message_id:'a', delta:'分析', created_at:1700000000};
  const events = [first, {type:'content',run_id:'r',source_message_id:'a',delta:'完成',created_at:1700000010}];
  const projection = new MessageProjection();
  assert.equal(projection.project([], [first], 'r')[0].created_at, first.created_at);
  assert.equal(projection.project([], events, 'r')[0].created_at, first.created_at);
  const replay = unwrapEvents(events.map((payload, i) => {const {created_at,...rest}=payload; return {payload:rest,created_at,sequence:i+1};}));
  assert.equal(displayMessages([], replay)[0].created_at, first.created_at);
  const saved = {id:'a',role:'assistant',content:'完成',created_at:1700000005};
  assert.deepEqual(projection.project([saved], events), [saved]);
  assert.equal(unwrapEvents([{payload:first,created_at:1800000000,sequence:1}])[0].created_at, first.created_at);
});

test('missing or invalid message time is never replaced with the current date', () => {
  for (const value of [undefined, null, 0, -1, NaN, Infinity, 1e20, '1700000000']) assert.equal(messageTime(value), null);
  assert.equal(messageTime(1700000000).iso, '2023-11-14T22:13:20.000Z');
  assert.equal(displayMessages([], [{type:'content',run_id:'r',delta:'旧记录'}])[0].created_at, undefined);
});

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

test('guidance divides live and saved output at receipt time without altering provider history', () => {
  const user={id:'u',role:'user',content:'开始',created_at:100,meta:{turn_id:'r'}};
  const guidance={id:'g',role:'user',content:'补充',created_at:103,meta:{turn_id:'r',kind:'user_guidance',ui_submitted_at:103.5}};
  const event=(type,time,extra={})=>({type,run_id:'r',source_message_id:'a',created_at:time,...extra});
  const events=[event('thinking',101,{delta:'先思考'}),event('content',102,{delta:'已完成第一步。'}),
    event('content',104,{delta:'继续第二步。'}),event('tool_call',105,{data:{id:'call',name:'read'}}),
    event('tool_result',106,{data:{id:'call',result:'结果'}}),event('content',108,{source_message_id:'b',delta:'完成'})];
  const projection=new MessageProjection();
  const before=projection.project([user],events.slice(0,2),'r');
  assert.equal(before[1].content,'已完成第一步。');
  const during=projection.project([user,guidance],events.slice(0,2),'r');
  assert.deepEqual(during.map(m=>m.id),['u','a:before:g','g']);
  assert.equal(during[1].live,false);
  const live=projection.project([user,guidance],events,'r');
  assert.deepEqual(live.map(m=>m.id),['u','a:before:g','g','a','tool:r:call','b']);
  assert.equal(live[1].content,'已完成第一步。');
  assert.equal(live[3].content,'继续第二步。');
  assert.equal(replyParts([live[1]]).answer,undefined);
  const saved=[user,guidance,{id:'a',role:'assistant',content:'已完成第一步。继续第二步。',reasoning_content:'先思考',tool_calls:[{id:'call',name:'read'}],meta:{turn_id:'r'}},
    {id:'t',role:'tool',content:'结果',tool_call_id:'call',meta:{turn_id:'r'}}, {id:'b',role:'assistant',content:'完成',meta:{turn_id:'r'}}];
  const original=structuredClone(saved), restored=displayMessages(saved,events);
  assert.deepEqual(restored.map(m=>[m.role,m.content]),live.map(m=>[m.role,m.content]));
  assert.deepEqual(saved,original);
  assert.equal(restored.filter(m=>m.live).length,0);
  // Missing event pages preserve all saved text and do not make up a split.
  const partial=displayMessages(saved,events.slice(2));
  assert.equal(partial.filter(m=>m.id==='a').length,1);
  assert.equal(partial.find(m=>m.id==='a').content,saved[2].content);
});

test('question receipts follow their tool stage across guidance and keep accepted answers on replay',()=>{
  const request={request_id:'q',kind:'choice',metadata:{tool_call_id:'call'},message:'确认方式'};
  const response={request_id:'q',status:'completed',text:'manual',selected_options:['manual']};
  const events=[{type:'interaction_finished',run_id:'r',data:{request_id:'q',answered:true,status:'completed'}},
    {type:'interaction_request',run_id:'r',source_message_id:'a',data:request},
    {type:'tool_result',run_id:'r',data:{id:'call',result_obj:{interaction_response:response}}}];
  const questions=interactionRecords(events);
  assert.deepEqual(questions[0].outcome.response,response);
  const groups=messageGroups([{id:'a',role:'assistant',tool_calls:[{id:'call'}],meta:{turn_id:'r'}},
    {id:'g',role:'user',content:'继续',meta:{turn_id:'r',same_turn_guidance:true}},
    {id:'b',role:'assistant',content:'完成',meta:{turn_id:'r'}},
    {id:'u2',role:'user',content:'下一轮',meta:{turn_id:'r2'}}],questions);
  assert.deepEqual(groups[0].questions,questions);
  assert.ok(groups.slice(1).every(group=>!group.questions));
  const durable=interactionRecords([{type:'interaction_request',run_id:'r',data:request},
    {type:'interaction_finished',run_id:'r',data:{request_id:'q',answered:true,status:'completed',response}}]);
  assert.deepEqual(durable[0].outcome.response,response);
});
