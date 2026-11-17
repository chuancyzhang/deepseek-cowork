import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('automation editor preserves drafts, distinguishes launch sources and keeps paused plans editable',{timeout:60000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/task-automation/renderer');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  assert.ok(data.startsWith(root+path.sep));
  try {
    const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import Capabilities from ${modulePath('management/Capabilities.tsx')};
import {StoreContext} from ${modulePath('host/context.tsx')};
import {HostStore} from ${modulePath('host/store.ts')};
import ${modulePath('styles.css')};
import ${modulePath('management.css')};
const store=new HostStore(),listeners=new Set(),clone=value=>JSON.parse(JSON.stringify(value));
let task={id:'weekly-report',revision:2,name:'周报整理',description:'每周归纳项目进展与待办事项。',prompt:'整理项目进展，列出已完成事项与下周计划。',skill_names:[],execution_mode:'chat',workspace_dir:'',schedule:{enabled:false,schedule_type:'weekly',schedule_summary:'每周一 09:00',weekdays:[0],time_of_day:'09:00',timezone:'Asia/Shanghai',timezone_label:'Asia/Shanghai',prompt_mode:'custom',custom_prompt:'汇总过去一周的进展，保存为周报。'},configuration_issues:[]};
window.calls=[];window.opened=[];window.failSave=false;
window.emit=event=>listeners.forEach(listener=>listener([event]));
window.cowork={onEvents:listener=>{listeners.add(listener);return()=>listeners.delete(listener);},call:async(method,p={})=>{
  window.calls.push({method,p:clone(p)});
  if(method==='favorites.list')return task?[clone(task)]:[];
  if(method==='skills.list')return [{name:'data-analysis',display_name:'数据分析'}];
  if(method==='favorites.save'){
    if(window.failSave){window.failSave=false;task={...task,revision:task.revision+1,name:'另一处修改的名称',description:'最新版本的用途'};throw Error('任务已变化，请重新读取后合并修改；当前输入尚未保存。');}
    if(p.value.id&&p.value.revision!==task.revision)throw Error('任务已变化，请读取最新版本。');
    const value=clone(p.value);task={...task,...value,id:value.id||'new-task',revision:(task?.revision||0)+1,
      schedule:value.schedule===null?null:{...task?.schedule,...value.schedule},configuration_issues:[]};return clone(task);
  }
  if(method==='favorites.run')return {status:'queued',conversation_id:p.source==='schedule'?'scheduled-chat':'task-chat',run_id:'fixture'};
  if(method==='favorites.delete'){task=null;return {deleted:true};}
  if(method==='favorites.history')return [{id:'history',favorite_name:'周报整理',status:'running',waiting_for_input:true,session_id:'scheduled-chat',started_at:1794906000}];
  throw Error(method);
}};
function Fixture(){const [kind,setKind]=useState('favorites'),[failure,setFailure]=useState('');window.setKind=setKind;
  return <StoreContext.Provider value={store}><div className="management-layout" style={{height:'100vh',background:'var(--bg)',color:'var(--text)'}}><nav className="management-nav"><button className={kind==='favorites'?'selected':''} onClick={()=>setKind('favorites')}>常用任务</button><button className={kind==='schedule_history'?'selected':''} onClick={()=>setKind('schedule_history')}>运行记录</button></nav><main className="management-content">{failure&&<p role="alert">{failure}</p>}<Capabilities key={kind} kind={kind} onError={setFailure} onDirty={()=>{}} confirm={async()=>1} openConversation={id=>window.opened.push(id)}/></main></div></StoreContext.Provider>;
}
createRoot(document.getElementById('root')).render(<Fixture/>);`);
    await writeFile(path.join(data,'index.html'),'<meta charset="utf-8"><div id="root"></div><script type="module" src="./entry.tsx"></script>');
    await build({configFile:false,root:data,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist'),rollupOptions:{input:path.join(data,'index.html')}}});
    const env={...process.env,COWORK_AUTOMATION_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'task-automation.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/TASK_AUTOMATION_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
