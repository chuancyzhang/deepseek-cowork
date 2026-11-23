import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('structured tool failures survive live display, detail, copy, history reload and session switching',{timeout:40000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/task-workspace-tests');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try {
    const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import TaskWorkspace from ${modulePath('TaskWorkspace.tsx')};
import MessageBubble,{AssistantTurn} from ${modulePath('MessageBubble.tsx')};
import {MessageProjection,messageGroups,turnTimings} from ${modulePath('runtime.ts')};
import {StoreContext} from ${modulePath('host/context.tsx')};
import {HostStore} from ${modulePath('host/store.ts')};
import ${modulePath('styles.css')};
const error={code:'bsk_command_failed',message:'no browser is currently connected',exit_code:1};
const result={status:'incomplete',error,stdout:'retained stdout',artifacts:[]};
const failed={id:'call',name:'browser_skill_cli',status:'incomplete',error,result,arguments:{args:['session','start']}};
const media={id:'media-call',name:'media_create',status:'queued',result:{task_id:'video-task',status:'queued'},arguments:{capability:'video_generate'}};
let snapshot=JSON.parse(localStorage.getItem('snapshot')||'null')||{run:{id:'r',status:'running'},calls:[]};
window.deliver=()=>{snapshot={run:{id:'r',status:'completed'},calls:[failed,media]};localStorage.setItem('snapshot',JSON.stringify(snapshot));};
window.cowork={call:async(method,params)=>{if(method==='clipboard.write'){window.copied=params.text;return;}if(method==='task.inspect')return params.conversation_id==='c'?snapshot:{run:{id:'other',status:'completed'},calls:[]};throw Error(method);}};
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
function Fixture(){const [history,setHistory]=useState(null),[cid,setCid]=useState('c'),[view,setView]=useState(()=>JSON.parse(localStorage.getItem('view')||'null')||{section:'calls'});
window.historyPreview=full=>setHistory({full});
if(history)return <StoreContext.Provider value={store}><HistoryPreview full={history.full} loadOlder={()=>setHistory({full:true})}/></StoreContext.Provider>;
return <StoreContext.Provider value={store}><button id="switch" onClick={()=>setCid(cid==='c'?'other':'c')}>切换会话</button><TaskWorkspace cid={cid} conversation={{id:cid,meta:{},run:{id:cid==='c'?'r':'other',status:'completed'}}} value={view} change={patch=>setView(prior=>{const next={...prior,...patch};localStorage.setItem('view',JSON.stringify(next));return next;})} error={message=>{throw Error(message);}}/>
<AssistantTurn cid="c" active={false} onAction={()=>{}} onFile={()=>{}} messages={[{id:'a',role:'assistant',tool_calls:[{id:'call',function:{name:'browser_skill_cli'}},{id:media.id,function:{name:media.name}}]},{id:'t',role:'tool',tool_call_id:'call',result_obj:result},{id:'mt',role:'tool',tool_call_id:media.id,result_obj:media.result},{id:'answer',role:'assistant',content:'已保存的最终回复'}]}/></StoreContext.Provider>;}
createRoot(document.getElementById('root')).render(<Fixture/>);`);
    await writeFile(path.join(data,'index.html'),'<div id="root"></div><script type="module" src="./entry.tsx"></script>');
    await build({configFile:false,root:data,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist'),rollupOptions:{input:path.join(data,'index.html')}}});
    const env={...process.env,COWORK_TASK_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'task-workspace.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/TASK_WORKSPACE_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
