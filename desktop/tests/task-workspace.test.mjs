import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('task details and independent subtask tabs preserve process, drafts, retries and session ownership',{timeout:65000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/task-workspace-tests');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try {
    const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import TaskWorkspace from ${modulePath('TaskWorkspace.tsx')};
import Workspace,{emptyWorkspace} from ${modulePath('Workspace.tsx')};
import MessageBubble,{AssistantTurn} from ${modulePath('MessageBubble.tsx')};
import {MessageProjection,messageGroups,turnTimings} from ${modulePath('runtime.ts')};
import {StoreContext} from ${modulePath('host/context.tsx')};
import {HostStore} from ${modulePath('host/store.ts')};
import ${modulePath('styles.css')};
import ${modulePath('management.css')};
import ${modulePath('workspace.css')};
import ${modulePath('composer.css')};
import ${modulePath('plugins/plugins.css')};
document.documentElement.style.setProperty('--font-family','"Microsoft YaHei UI",sans-serif');
const error={code:'bsk_command_failed',message:'no browser is currently connected',exit_code:1};
const result={status:'incomplete',error,stdout:'retained stdout',artifacts:[]};
const failed={id:'call',name:'browser_skill_cli',status:'incomplete',error,result,arguments:{args:['session','start']}};
const media={id:'media-call',name:'media_create',status:'queued',result:{task_id:'video-task',status:'queued'},arguments:{capability:'video_generate'}};
let snapshot=JSON.parse(localStorage.getItem('snapshot')||'null')||{run:{id:'r',status:'running'},calls:[]};
window.deliver=()=>{snapshot={run:{id:'r',status:'completed'},calls:[failed,media]};localStorage.setItem('snapshot',JSON.stringify(snapshot));};
window.cowork={onEvents:()=>()=>{},call:async(method,params)=>{if(method==='clipboard.write'){window.copied=params.text;return;}if(method==='task.output')return null;if(method==='task.inspect')return params.conversation_id==='c'?snapshot:{run:{id:'other',status:'completed'},calls:[]};throw Error(method);}};
const store=new HostStore();store.set('reply:c:a',true);
const oldStage={id:'old-stage',role:'assistant',content:'已完成资料核对。',reasoning_content:'先核对资料来源。',tool_calls:[{id:'old-call',function:{name:'read_file',arguments:'{}'}}],meta:{turn_id:'old'}};
const oldResult={id:'old-result',role:'tool',content:'资料已读取。',tool_call_id:'old-call',meta:{turn_id:'old'}};
const historyPage=[{id:'old-answer',role:'assistant',content:'资料核对已完成，可以继续下一步。',reasoning_content:'按来源整理结论。',meta:{turn_id:'old'}},
  {id:'new-question',role:'user',content:'请根据这些资料给出建议。',meta:{turn_id:'new'}},
  {id:'new-answer',role:'assistant',content:'建议先确认目标，再逐项落实。',reasoning_content:'结合已核对的资料提出建议。',meta:{turn_id:'new'}}];
const historyEvents=[{type:'turn_started',run_id:'old',created_at:100},
  {type:'thinking',run_id:'old',source_message_id:'old-stage',delta:'先核对资料来源。',persisted_position:1},
  {type:'tool_call',run_id:'old',source_message_id:'old-stage',data:oldStage.tool_calls[0],persisted_position:1},
  {type:'tool_result',run_id:'old',source_message_id:'old-stage',data:{id:'old-call',result:'资料已读取。'},persisted_position:2},
  {type:'final',run_id:'old',created_at:428},{type:'turn_started',run_id:'new',created_at:500},{type:'final',run_id:'new',created_at:502}];
function HistoryPreview({full,loadOlder}){
  const saved=full?[{id:'old-question',role:'user',content:'请先核对资料并整理结论。',meta:{turn_id:'old'}},oldStage,oldResult,...historyPage]:historyPage;
  const issues=[],messages=new MessageProjection().project(saved,historyEvents,undefined,issue=>issues.push(issue));
  const groups=messageGroups(messages),timings=turnTimings(historyEvents);window.historyProjection={ids:messages.map(m=>m.id),issues};
  return <div className="history-fixture" style={{maxWidth:760,padding:24,margin:'0 auto'}}>{!full&&<button className="load-history" onClick={loadOlder}>更早的消息</button>}{groups.map(group=><div key={group.id} data-turn={group.messages[0].meta.turn_id}>{group.assistant?<AssistantTurn cid="history" messages={group.messages} active={false} timing={timings.get(group.messages[0].meta.turn_id)} onAction={()=>{}} onFile={()=>{}}/>:<MessageBubble cid="history" message={group.messages[0]} active={false} onAction={()=>{}} onFile={()=>{}}/>}</div>)}</div>;
}
const now=Date.now()/1000;
const agents=[{id:'runtime',name:'Runtime',status:'running',run_id:'child-run',started_at:now-73,duration:73,latest_progress:'正在检查消息归属与停止恢复',meta:{}},{id:'e2e',name:'E2e',status:'completed',run_id:'e2e-run',duration:48,latest_progress:'正常发送、切换和重开均已验证',meta:{}}];
const childData={runtime:{summary:agents[0],messages:[{id:'request',role:'user',content:'检查并行任务的消息隔离与停止恢复，保留现有权限边界。'},{id:'child-run:assistant',role:'assistant',reasoning_content:'先检查消息归属，再验证停止与恢复时的输入保留。',content:'消息按任务隔离，正在验证失败后重试。'}],inbox:[],activity:[{run_id:'child-run',sequence:1,payload:{event_type:'tool_call',tool_call_id:'child-tool',tool_name:'read_file',tool_args:{path:'core/agent_manager.py'}}},{run_id:'child-run',sequence:2,payload:{event_type:'tool_result',tool_call_id:'child-tool',tool_result:'完整的子任务工具结果'}}]},e2e:{summary:agents[1],messages:[{id:'e2e-run:assistant',role:'assistant',content:'已完成代表性流程验证。'}],inbox:[],activity:[]}};
const parentMessages=[{id:'parent-stage',role:'assistant',content:'我会把运行逻辑和端到端验证拆开检查，再统一汇总。',reasoning_content:'保持任务之间的输入与结果隔离。',tool_calls:[{id:'spawn-runtime',function:{name:'spawn_agent',arguments:JSON.stringify({name:'Runtime',message:'检查消息隔离与停止恢复。'})}},{id:'spawn-e2e',function:{name:'spawn_agent',arguments:JSON.stringify({name:'E2e',message:'验证发送、切换与重开。'})}}],meta:{turn_id:'parent-run'}},{id:'spawn-result',role:'tool',tool_call_id:'spawn-runtime',result_obj:{agent_id:'runtime',name:'Runtime',status:'spawned'}},{id:'spawn-e2e-result',role:'tool',tool_call_id:'spawn-e2e',result_obj:{agent_id:'e2e',name:'E2e',status:'spawned'}},{id:'parent-send',role:'assistant',tool_calls:[{id:'send-runtime',function:{name:'send_input',arguments:JSON.stringify({target:'runtime',message:'保留已有权限校验。'})}}]},{id:'send-result',role:'tool',tool_call_id:'send-runtime',result_obj:{agent_id:'runtime',status:'queued'}},{id:'parent-answer',role:'assistant',content:'已开始分工，结果会在各子任务中持续更新。'}];
const originalCall=window.cowork.call;window.agentCalls=[];
window.cowork.call=async(method,p={})=>{
  if(method==='task.inspect' && window.contextMode)return {run:{id:p.run_id || 'r',status:'completed'},calls:[],agents:[],current_context:{workspace:'D:/projects/research',model:'DeepSeek',mode:'普通任务'},submitted_context:{workspace:'D:/projects/research',model:'DeepSeek',skills:['资料核对']}};
  if(method==='task.context' && window.contextMode)throw Error('该轮的模型请求快照已被后续请求替换。请返回当前任务后查看最新请求。');
  if(method==='workbench.list')return {tabs:[]};if(method==='workbench.profiles')return [];
  if(method==='agent.list')return p.conversation_id==='c'?structuredClone(agents):[];
  if(method==='agent.get'){const value=structuredClone(childData[p.agent_id]);if(window.delayAgent===p.agent_id){window.delayAgent=null;await new Promise(resolve=>window.releaseAgent=resolve);}return value;}
  if(method.startsWith('agent.')){window.agentCalls.push({method,p});const item=childData[p.agent_id];
    if(method==='agent.send'){if(window.failSend){window.failSend=false;throw Error('发送失败，补充内容已保留。');}if(window.delaySend)await new Promise(resolve=>window.releaseSend=resolve);if(!item.messages.some(message=>message.id===p.message_id))item.messages.push({id:p.message_id,role:'user',content:p.content});}
    if(method==='agent.stop')item.summary.status='interrupted';if(method==='agent.resume')item.summary.status='running';return {ok:true};}
  return originalCall(method,p);
};
function SubtasksPreview(){
  const [cid,setCid]=useState('c'),[views,setViews]=useState(()=>JSON.parse(localStorage.getItem('agentViews')||'{}'));
  const value=views[cid] || {...emptyWorkspace(),selected:'agents',agentsOpen:true,agents:{section:'agents'}};
  const change=patch=>setViews(prior=>{const next={...prior,[cid]:{...(prior[cid]||value),...patch}};localStorage.setItem('agentViews',JSON.stringify(next));return next;});
  window.agentView=value;window.restoreLegacy=(opened=false)=>setViews({c:{...emptyWorkspace(),...(opened ? {agents:{section:'agents',agentId:'runtime'}} : {}),task:{section:'calls',drafts:{runtime:{text:'旧版子任务草稿'}},scroll:{'calls:::':12}}}});window.selectAgent=id=>change({selected:'agents',agentsOpen:true,agents:{...(value.agents||{}),section:'agents',agentId:id,callId:undefined}});
  const no=async()=>{},conversation={id:cid,title:'检查子任务协作',meta:{},run:{id:'parent-run',status:'completed'}},error=message=>{throw Error(message);};
  return <StoreContext.Provider value={store}><div className="subtasks-fixture" style={{display:'flex',height:'100vh','--workspace-width':'48%',background:'var(--bg)',color:'var(--text)'}}><main style={{flex:1,minWidth:0,padding:28,overflow:'auto'}}>
    <h2>检查子任务协作</h2><AssistantTurn cid={cid} messages={parentMessages} timing={{id:'parent-run',start:now-80,end:now,thinking:{}}} active={false} onFile={no} onAction={(action,message)=>{if(action==='agent')window.selectAgent(message.meta.ui_agent_target);}}/>
    <button id="agent-switch" onClick={()=>setCid(cid==='c'?'other':'c')}>切换会话</button>
  </main><Workspace key={cid} conversation={conversation} value={value} change={change} expanded={false} expand={no} close={no} obscured={false} dark={false} editors={[]} fileTree={{files:[]}} fileContent={null} libraryContent={null} selectFile={no} openFile={no} closeFile={no} refreshFiles={no} createDocument={no} preserve={no} openReview={no} registerEditorFlush={()=>{}} error={error} confirm={async()=>1}/></div></StoreContext.Provider>;
}
const streamCall=window.cowork.call;
window.streamText='one\\ntwo\\n'+('x'.repeat(1000));window.streamRunning=true;window.streamReads=0;
window.cowork.call=async(method,p)=>{
  if(!window.streamMode)return streamCall(method,p);
  if(method==='task.output'){
    window.streamReads++;if(window.delayStream){window.delayStream=false;await new Promise(resolve=>window.releaseStream=resolve);}
    const text=p.conversation_id==='stream' ? window.streamText : 'OTHER SESSION';
    return {preview:text,path:'saved-log.jsonl',bytes:200,status:window.streamRunning?'running':'succeeded',incomplete:window.streamIncomplete,reason:window.streamIncomplete?'日志已达上限，后续输出已省略。':'',started_at:now-70,finished_at:window.streamRunning?undefined:now,records:[{sequence:1,channel:'stdout',text}],cursor:200,has_more:false};
  }
  if(method==='task.inspect')return {run:{id:'stream-run',status:window.streamRunning?'running':'completed'},calls:[{id:'stream-call',name:'run_node_code',arguments:{code:'console.log(42)'},status:window.streamRunning?'running':'completed',result:window.streamRunning?null:'done'}]};
  if(method==='clipboard.write')return;
  return streamCall(method,p);
};
function StreamingPreview(){
  const [cid,setCid]=useState('stream'),[running,setRunning]=useState(true),[view,setView]=useState({section:'calls'}),[detail,setDetail]=useState(false);
  window.finishStream=()=>{window.streamRunning=false;setRunning(false);};window.switchStream=()=>setCid(value=>value==='stream'?'other-stream':'stream');
  const messages=[{id:'stream-stage',role:'assistant',reasoning_content:'逐项检查目录。',tool_calls:[{id:'stream-call',function:{name:'run_node_code',arguments:'{}'}}]},
    {id:'stream-result',role:'tool',tool_call_id:'stream-call',content:'',result_obj:{background_tool:true,status:running?'running':'completed',task_id:'task'},meta:{turn_id:'stream-run'}},
    {id:'wait-stage',role:'assistant',tool_calls:[{id:'wait-call',function:{name:'wait_tool',arguments:'{"task_id":"task"}'}}],meta:{turn_id:'stream-run'}}];
  return <StoreContext.Provider value={store}><div style={{display:'flex',height:'100vh',background:'var(--bg)',color:'var(--text)'}}><main id="stream-main" style={{flex:1,minWidth:0,padding:24,overflow:'auto'}}><textarea id="stream-input" defaultValue="保留输入"/>
    <AssistantTurn key={cid} cid={cid} messages={messages} active={running} activeRun={running?'stream-run':undefined} timing={{id:'stream-run',start:now-70,end:running?undefined:now,thinking:{}}} onFile={()=>{}} onAction={(action,message)=>{setDetail(true);setView({section:'calls',callId:message.tool_call_id});}}/>
    <div style={{height:1200}}>继续阅读</div></main>{detail&&<aside style={{width:'46%',display:'flex',minWidth:0}}><TaskWorkspace key={cid} cid={cid} conversation={{id:cid,meta:{},run:{id:'stream-run',status:running?'running':'completed'}}} value={view} change={patch=>setView(value=>({...value,...patch}))} error={()=>{}}/></aside>}</div></StoreContext.Provider>;
}
function Fixture(){const [stream,setStream]=useState(false);window.streamingPreview=()=>{window.streamMode=true;setStream(true);};const [context,setContext]=useState(false),[subtasks,setSubtasks]=useState(false),[history,setHistory]=useState(null),[cid,setCid]=useState('c'),[view,setView]=useState(()=>JSON.parse(localStorage.getItem('view')||'null')||{section:'calls'});
window.historyPreview=full=>setHistory({full});window.subtasksPreview=()=>setSubtasks(true);window.contextPreview=()=>{window.contextMode=true;setSubtasks(false);setHistory(null);setContext(true);setView({section:'context',runId:'historical'});};
if(stream)return <StreamingPreview/>;
if(context)return <StoreContext.Provider value={store}><div style={{height:'100vh',display:'flex',background:'var(--bg)'}}><TaskWorkspace cid="c" conversation={{id:'c',meta:{project_name:'客户研究'},run:{id:'r',status:'completed'}}} value={view} change={patch=>setView(prior=>({...prior,...patch}))} error={message=>{throw Error(message);}}/></div></StoreContext.Provider>;
if(subtasks)return <SubtasksPreview/>;
if(history)return <StoreContext.Provider value={store}><HistoryPreview full={history.full} loadOlder={()=>setHistory({full:true})}/></StoreContext.Provider>;
return <StoreContext.Provider value={store}><button id="switch" onClick={()=>setCid(cid==='c'?'other':'c')}>切换会话</button><TaskWorkspace cid={cid} conversation={{id:cid,meta:{},run:{id:cid==='c'?'r':'other',status:'completed'}}} value={view} change={patch=>setView(prior=>{const next={...prior,...patch};localStorage.setItem('view',JSON.stringify(next));return next;})} error={message=>{throw Error(message);}}/>
<AssistantTurn cid="c" active={false} onAction={()=>{}} onFile={()=>{}} messages={[{id:'a',role:'assistant',tool_calls:[{id:'call',function:{name:'browser_skill_cli'}},{id:media.id,function:{name:media.name}}]},{id:'t',role:'tool',tool_call_id:'call',result_obj:result},{id:'mt',role:'tool',tool_call_id:media.id,result_obj:media.result},{id:'answer',role:'assistant',content:'已保存的最终回复'}]}/></StoreContext.Provider>;}
createRoot(document.getElementById('root')).render(<Fixture/>);`);
    await writeFile(path.join(data,'index.html'),'<meta charset="utf-8"><div id="root"></div><script type="module" src="./entry.tsx"></script>');
    await build({configFile:false,root:data,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist'),rollupOptions:{input:path.join(data,'index.html')}}});
    const env={...process.env,COWORK_TASK_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'task-workspace.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/TASK_WORKSPACE_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
