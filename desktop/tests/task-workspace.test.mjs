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
import {AssistantTurn} from ${modulePath('MessageBubble.tsx')};
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
function Fixture(){const [cid,setCid]=useState('c'),[view,setView]=useState(()=>JSON.parse(localStorage.getItem('view')||'null')||{section:'calls'});
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
