import assert from 'node:assert/strict';
import test from 'node:test';
import { contextRecoveryForRun, displayMessages, mergeEvents, messageGroups, replyParts, MessageProjection, unwrapEvents, messageTime, conversationTime, turnTimings, elapsedTime, thinkingTime, interactionRecords, isUserStopped, toolErrorText } from '../src/runtime.ts';
import { UsageProjection, generationRates } from '../src/usageMetrics.ts';
import { conversationFiles } from '../src/attachments.ts';
import {agentRecords, childMessages} from '../src/subagents.ts';

test('collaboration records preserve exact targets, failed calls and historical result shapes',()=>{
  const call=(id,name,args)=>({id:'message-'+id,role:'assistant',tool_calls:[{id,function:{name,arguments:JSON.stringify(args)}}]});
  const result=(id,data)=>({id:'result-'+id,role:'tool',tool_call_id:id,content:JSON.stringify(data)});
  const messages=[call('spawn','spawn_agent',{name:'Runtime',message:'检查运行'}),result('spawn',{agent_id:'a',name:'Runtime'}),
    call('wait','wait_agent',{targets:['a','b']}),result('wait',{completed:[{id:'a',name:'Runtime'}],pending:[{id:'b',name:'E2e'}]}),
    call('failed','spawn_agent',{name:'Review'}),result('failed',{error:{message:'没有可用执行槽'}}),
    call('missing','spawn_agent',{}),result('missing',null)];
  const original=structuredClone(messages),records=agentRecords(messages,replyParts(messages).results);
  assert.deepEqual(records.map(item=>[item.target,item.name]),[['a','Runtime'],['a','Runtime'],['b','E2e'],['','Review'],['','']]);
  assert.equal(records[3].failed,true);
  assert.equal(records[3].callId,'failed');
  assert.deepEqual(messages,original);
});

test('child projection isolates runs and retains text, structured failures and partial output',()=>{
  const activity=[{run_id:'one',payload:{event_type:'tool_call',tool_call_id:'read',tool_name:'read_file',tool_args:{path:'notes.md'}}},
    {run_id:'one',payload:{event_type:'tool_result',tool_call_id:'read',tool_result:'原始文本结果'}},
    {run_id:'two',payload:{event_type:'tool_call',tool_call_id:'write',tool_name:'write_file',tool_args:{path:'report.md'}}},
    {run_id:'two',payload:{event_type:'tool_result',tool_call_id:'write',tool_result_obj:{error:{message:'保存失败'}}}}];
  const child={summary:{run_id:'two',status:'running'},messages:[{id:'seed-system',role:'system',content:'内部配置'},
    {id:'one:assistant',role:'assistant',content:'第一轮成果',reasoning_content:'第一轮思考'},
    {id:'question',role:'user',content:'继续'}, {id:'two:assistant',role:'assistant',content:'已输出部分',reasoning_content:'第二轮思考'}],activity:[...activity,...activity]};
  const original=structuredClone(child),groups=messageGroups(childMessages(child));
  assert.equal(groups.length,3);
  assert.equal(groups[0].messages.filter(item=>item.role==='tool').length,1);
  assert.equal(replyParts(groups[0].messages).results.get('read').content,'原始文本结果');
  assert.equal(replyParts(groups[0].messages).results.get('write'),undefined);
  assert.deepEqual(replyParts(groups[2].messages).results.get('write').result_obj.error,{message:'保存失败'});
  assert.equal(replyParts(groups[2].messages).answer.content,'已输出部分');
  assert.equal(replyParts(groups[2].messages).answer.live,true);
  assert.deepEqual(child,original);
});

const providerEvent = (run_id, type, created_at, status) => ({
  type: 'observability', run_id, created_at, data: { type, request_id: run_id + ':request:1', status },
});

test('generation speed uses recorded deltas and replaces snapshots without counting them twice', () => {
  const projection = new UsageProjection();
  const run_id = 'speed-run';
  const events = [
    providerEvent(run_id, 'provider_request_start', 100),
    { type: 'thinking', run_id, created_at: 103, delta: '思考' },
    { type: 'content', run_id, created_at: 104, delta: 'ab' },
    { type: 'content', run_id, created_at: 105, delta: 'cdefg' },
    { type: 'content_snapshot', run_id, created_at: 106, content: 'abcdefghi' },
    { type: 'content_snapshot', run_id, created_at: 107, content: 'abcdefghi' },
  ];
  const { request } = projection.project(events, run_id);
  const rates = generationRates(request, 106);
  assert.deepEqual(rates.slice(-4), [undefined, 3, 2, 5 / 3]);
  assert.equal(generationRates(request, 110).at(-1), 0);

  const complete = [
    ...events,
    providerEvent(run_id, 'provider_request_finish', 108, 'completed'),
  ];
  assert.equal(projection.project(complete, run_id).request, undefined);
  assert.equal(projection.project([...complete], undefined).lastSpeed, 1);
  assert.equal(new UsageProjection().project(complete).lastSpeed, 1);
});

test('a retry or interruption does not replace the previous completed speed', () => {
  const projection = new UsageProjection();
  const previous = [
    providerEvent('earlier', 'provider_request_start', 100),
    { type: 'content', run_id: 'earlier', created_at: 102, delta: '完成' },
    providerEvent('earlier', 'provider_request_finish', 104, 'completed'),
  ];
  const inProgress = [
    ...previous,
    providerEvent('current', 'provider_request_start', 110),
    { type: 'content', run_id: 'current', created_at: 111, delta: '初次输出' },
    providerEvent('current', 'provider_retry', 114),
  ];
  assert.deepEqual(generationRates(projection.project(inProgress, 'current').request, 115), []);
  const interrupted = projection.project([
    ...inProgress,
    { type: 'thinking', run_id: 'current', created_at: 116, delta: '再试' },
    providerEvent('current', 'provider_request_finish', 117, 'interrupted'),
  ], 'current');
  assert.equal(interrupted.request, undefined);
  assert.equal(interrupted.lastSpeed, 1);
  assert.equal(projection.project([]).lastSpeed, undefined);
});

test('usage pairs each request with its own budget, including legacy and disabled runs', () => {
  const projection = new UsageProjection();
  const observation = (run_id, data) => ({type: 'observability', run_id, data});
  const budget = {status: 'available', phase: 'ready', used_tokens: 40830, effective_tokens: 300000};
  const text = {status: 'available', total: 35000};
  const events = [observation('old', {type: 'context_capacity', capacity: budget}),
    observation('old', {type: 'system_prompt', context_usage: text}),
    observation('old', {type: 'context_capacity', capacity: {...budget, phase: 'finished', used_tokens: 90000}})];
  assert.deepEqual(projection.project(events).capacity, budget);
  const fresh = [...events, observation('off', {type: 'system_prompt', context_usage: text})];
  assert.equal(projection.project(fresh, 'off').capacity, undefined);
  const limited = {...budget, used_tokens: 50000, effective_tokens: 466808, target_tokens: 1000000};
  const latest = [...fresh, observation('new', {type: 'system_prompt', context_usage: text, context_capacity: limited})];
  assert.deepEqual(projection.project(latest, 'new').capacity, limited);
  assert.equal(projection.project(latest, 'new').contextRunId, 'new');
});

test('the latest turn sums reported requests without inheriting another turn’s usage', () => {
  const projection = new UsageProjection();
  const receipt = (run_id, request_id, input_tokens, output_tokens, cached_input_tokens) => ({
    type: 'observability', run_id, data: {type:'llm_usage', usage:{request_id,input_tokens,output_tokens,cached_input_tokens}},
  });
  const second = receipt('current', 'two', 30, 10);
  const events = [receipt('prior','one',40,10,20), receipt('current','one',100,20,60), second, second];
  const active = projection.project(events, 'current');
  assert.deepEqual([active.turnTokens, active.input + active.output, active.cached, active.cacheInput], [160, 210, 80, 140]);
  assert.equal(projection.project(events, undefined, 'current').turnTokens, 160);
  assert.equal(projection.project(events, 'waiting').turnTokens, undefined);
  const history = projection.project([receipt('older','one',20,5,0), ...events], undefined, 'current');
  assert.deepEqual([history.turnTokens, history.input + history.output], [160, 235]);
  assert.equal(projection.project([]).turnTokens, undefined);
});

test('conversation files combine confirmed reply files and sent attachments by path', () => {
  const input = 'D:/docs/资料.txt';
  const same = 'd:\\docs\\资料.txt';
  const output = 'D:/docs/output/纪要.md';
  assert.deepEqual(conversationFiles([
    {role:'user',meta:{user_added_files:[input]}},
    {role:'assistant',meta:{hidden:true},file_references:[{path:'D:/skill/SKILL.md'}]},
    {role:'tool',file_references:[{path:'D:/implementation/run.log'}]},
    {role:'assistant',file_references:[{path:same,name:'资料.txt'},{path:output,name:'output/纪要.md'}]},
    {role:'user',meta:{desktop_attachments:[{path:'D:/docs/表格.csv',name:'表格.csv'}]}},
  ]), [
    {path:'D:/docs/表格.csv',name:'表格.csv'}, {path:output,name:'纪要.md'}, {path:same,name:'资料.txt'},
  ]);
});

test('context recovery follows the failed run after a reload', () => {
  const events = unwrapEvents([{payload: {type: 'final', run_id: 'old', result: {context_recovery: 'increase_target'}}},
    {payload: {type: 'final', run_id: 'new', result: {context_recovery: 'new_chat'}}}]);
  assert.equal(contextRecoveryForRun(events, 'old'), 'increase_target');
  assert.equal(contextRecoveryForRun(events, 'new'), 'new_chat');
  assert.equal(contextRecoveryForRun(events, 'other'), undefined);
});

test('tool error display accepts structured JSON without changing the retained error', () => {
  const error = {code:'bsk_command_failed',message:'no browser is currently connected',exit_code:1};
  const before = JSON.stringify(error);
  assert.equal(toolErrorText(error), error.message);
  assert.equal(JSON.stringify(error), before);
  assert.equal(toolErrorText('连接失败'), '连接失败');
  for (const value of [null, undefined]) assert.equal(toolErrorText(value), '');
  for (const value of [{code:'failed',exit_code:1}, {message:{reason:'offline'}}, [{code:'failed'}]]) {
    assert.deepEqual(JSON.parse(toolErrorText(value)), value);
  }
  assert.equal(toolErrorText(1), '1');
});

test('only explicit user cancellation suppresses the error notice', () => {
  for (const error of ['interrupted by user', 'Run interrupted by user.']) assert.equal(isUserStopped({status:'interrupted',error}),true);
  for (const error of ['应用上次退出时任务尚未结束', '对话保存冲突', 'connection timeout', '']) assert.equal(isUserStopped({status:'interrupted',error}),false);
  assert.equal(isUserStopped({status:'failed',error:'Run interrupted by user.'}),false);
  assert.equal(isUserStopped(null),false);
});

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

test('sidebar update time uses calendar days across a year boundary', () => {
  const now = new Date(2026, 0, 1, 10);
  const seconds = (...parts) => new Date(...parts).getTime() / 1000;
  assert.equal(conversationTime(seconds(2026, 0, 1, 9, 5), now).label, '09:05');
  assert.equal(conversationTime(seconds(2025, 11, 31, 21), now).label, '昨天');
  assert.equal(conversationTime(seconds(2025, 11, 30, 15), now).label, '12月30日');
  assert.equal(conversationTime(0, now), null);
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

test('page-excluded saved sources and tool results never reappear at the tail, including after reload and backfill', () => {
  const msg=(id,role,turn,extra={})=>({id,role,content:id,meta:{turn_id:turn},...extra});
  const first=msg('a1','assistant','first'),tool=msg('t1','tool','first',{tool_call_id:'call'});
  const page=[msg('a2','assistant','first'),msg('u2','user','last'),msg('a3','assistant','last')];
  const raw=[
    {sequence:1,persisted_position:0,payload:{type:'content',run_id:'first',source_message_id:'a1',delta:'a1'}},
    {sequence:2,persisted_position:1,payload:{type:'tool_result',run_id:'first',source_message_id:'a1',data:{id:'call',result:'t1'}}},
    {sequence:3,created_at:100,payload:{type:'turn_started',run_id:'first'}},
    {sequence:4,created_at:428,payload:{type:'final',run_id:'first'}},
  ];
  const original=structuredClone(raw),events=unwrapEvents(raw),projection=new MessageProjection();
  assert.deepEqual(projection.project(page,events),page);
  assert.deepEqual(messageGroups(projection.project(page,unwrapEvents(structuredClone(raw)))).map(g=>g.messages[0].meta.turn_id),['first','last','last']);
  const older=[msg('u1','user','first'),first,tool,...page];
  assert.deepEqual(projection.project(older,events),older);
  assert.deepEqual(new MessageProjection().project(page,events),page);
  assert.equal(elapsedTime(turnTimings(events).get('first').start,turnTimings(events).get('first').end),328);
  assert.deepEqual(raw,original);
});

test('unsaved interrupted fragments follow source order within a partial turn and reconcile after persistence', () => {
  const saved=[{id:'a','role':'assistant',content:'first stage',meta:{turn_id:'r'}},
    {id:'z',role:'assistant',content:'later stage',meta:{turn_id:'r'}},
    {id:'next',role:'user',content:'next question',meta:{turn_id:'next'}},
    {id:'answer',role:'assistant',content:'next answer',meta:{turn_id:'next'}}];
  const events=[{type:'content',run_id:'r',sequence:1,source_message_id:'a',delta:'first stage'},
    {type:'thinking',run_id:'r',sequence:2,source_message_id:'partial',delta:'retained thinking'},
    {type:'tool_result',run_id:'r',sequence:3,source_message_id:'partial',data:{id:'call',result:'retained result'}},
    {type:'content',run_id:'r',sequence:4,source_message_id:'z',delta:'later stage'},
    {type:'final',run_id:'r',sequence:5}];
  const projection=new MessageProjection(),issues=[];
  const out=projection.project(saved,events,undefined,issue=>issues.push(issue));
  assert.deepEqual(out.map(m=>m.id),['a','partial','tool:r:call','z','next','answer']);
  assert.equal(out[1].reasoning_content,'retained thinking');
  assert.equal(out[1].live,false);
  assert.deepEqual(issues,[]);
  // A later snapshot knows the source is now saved, even if the page moved past it.
  const snapshot=events.map(e=>({...e,persisted_position:e.sequence===2?2:e.sequence===3?3:undefined}));
  assert.deepEqual(projection.project(saved,mergeEvents(snapshot,events)),saved);
});

test('tool-result deduplication is scoped by turn, not by the assistant source or another turn call', () => {
  const saved=[{id:'u1',role:'user',content:'one',meta:{turn_id:'r1'}},
    {id:'t1',role:'tool',tool_call_id:'same-call',content:'one result',meta:{turn_id:'r1'}},
    {id:'u2',role:'user',content:'two',meta:{turn_id:'r2'}},
    {id:'a2',role:'assistant',content:'saved caller',meta:{turn_id:'r2'}}];
  const events=[{type:'tool_result',run_id:'r2',source_message_id:'a2',data:{id:'same-call',result:'two result'}}];
  assert.equal(displayMessages(saved,events).at(-1).content,'two result');
});

test('unlocatable output is retained for a later anchor and reported instead of appended to another turn', () => {
  const projection=new MessageProjection(),issues=[];
  const events=[{type:'content',run_id:'old',source_message_id:'partial',delta:'retained'}];
  const next=[{id:'u2',role:'user',content:'next',meta:{turn_id:'next'}}];
  assert.deepEqual(projection.project(next,events,undefined,issue=>issues.push(issue)),next);
  assert.deepEqual(issues,[{runId:'old',messageId:'partial',reason:'missing_turn'}]);
  const restored=projection.project([{id:'u1',role:'user',content:'one',meta:{turn_id:'old'}},...next],events);
  assert.deepEqual(restored.map(m=>m.id),['u1','partial','u2']);
  assert.equal(restored[1].content,'retained');
});

test('a legacy event without source identity cannot duplicate a saved assistant', () => {
  const saved=[{id:'u',role:'user',content:'question',meta:{turn_id:'r'}},
    {id:'a',role:'assistant',content:'saved answer',meta:{turn_id:'r'}}];
  const issues=[],events=[{type:'content',run_id:'r',delta:'saved answer'}];
  assert.deepEqual(new MessageProjection().project(saved,events,undefined,issue=>issues.push(issue)),saved);
  assert.equal(issues[0].reason,'missing_source');
});

test('a turn match alone cannot establish order relative to a saved answer with no source events', () => {
  const saved=[{id:'answer',role:'assistant',content:'saved answer',meta:{turn_id:'r'}}];
  const issues=[],events=[{type:'content',run_id:'r',source_message_id:'partial',delta:'retained stage'}];
  const projection=new MessageProjection();
  assert.deepEqual(projection.project(saved,events,undefined,issue=>issues.push(issue)),saved);
  assert.equal(issues[0].reason,'missing_order');
  const complete=[...events,{type:'content',run_id:'r',source_message_id:'answer',delta:'saved answer'}];
  assert.deepEqual(projection.project(saved,complete).map(m=>m.id),['partial','answer']);
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
