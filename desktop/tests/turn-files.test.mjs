import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
import {turnFileRows} from '../src/turnFiles.ts';
import {displayMessages} from '../src/runtime.ts';
const require=createRequire(import.meta.url),desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');

test('turn rows coalesce equivalent paths and keep historical navigation after eviction',()=>{
  const record={id:'r',path:'D:\\project\\a.py',name:'a.py',state:'purged'};
  const rows=turnFileRows([{path:'d:/project/a.py',name:'a.py'},{path:'D:/project/guide.md',name:'guide.md'}],[record]);
  assert.equal(rows.length,2);assert.equal(rows[0].record,record);assert.equal(rows[1].record,undefined);
});

test('guidance splitting attaches file history only to the last fragment',()=>{
  const meta={turn_id:'run'},record={id:'record',run_id:'run'};
  const messages=[{id:'answer',role:'assistant',content:'beforeafter',meta,turn_files:[record]},
    {id:'guidance',role:'user',content:'补充',created_at:20,meta:{...meta,same_turn_guidance:true}}];
  const events=[{type:'content',run_id:'run',source_message_id:'answer',created_at:10,delta:'before'},
    {type:'content',run_id:'run',source_message_id:'answer',created_at:30,delta:'after'}];
  const result=displayMessages(messages,events);
  assert.equal(result.flatMap(message=>message.turn_files||[]).length,1);
  assert.equal(result.at(-1).turn_files[0],record);
});

test('native file cards retain turns and isolate history, current-file actions and late responses',{timeout:60000},async()=>{
  const root=path.resolve(desktop,'../.tmp/turn-files');await mkdir(root,{recursive:true});
  const data=await mkdtemp(path.join(root,'ui-'));
  try {
    const source=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'index.html'),'<div id="root"></div><script type="module" src="/entry.tsx"></script>');
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import MessageBubble,{AssistantTurn} from ${source('MessageBubble.tsx')};import {FileHistoryPanel} from ${source('WorkspaceChanges.tsx')};
import {StoreContext} from ${source('host/context.tsx')};import {HostStore} from ${source('host/store.ts')};
import {themeStyle} from ${source('theme.ts')};
import ${source('styles.css')};import ${source('workspace.css')};
const store=new HostStore(),no=()=>{};
Object.entries(themeStyle({})).forEach(([key,value])=>document.documentElement.style.setProperty(key,value));
const record={id:'record-a',run_id:'first',path:'D:/project/dags/voice_recording_to_hive_ods.py',name:'dags/voice_recording_to_hive_ods.py',created_at:1795529940,state:'available',status:'modified',added:2,removed:1};
const backup={...record,id:'backup',path:'D:/project/voice_recording_to_hive_ods.before.py',name:'voice_recording_to_hive_ods.before.py',status:'added',added:1,removed:0};
const reference={path:'D:/project/docs/processing.md',name:'docs/processing.md',kind:'markdown',label:'MD',references:['docs/processing.md'],url:'file:///D:/project/docs/processing.md'};
window.opened=[];window.mode='available';window.hold=false;
window.cowork={call:async(method,p)=>{
 if(method==='task.output')return null;
 if(method!=='workspace.file_history')throw Error(method);
 const value={...record,id:p.id,state:window.mode,patch:window.mode==='available'?'--- before\\n+++ after\\n@@ -1 +1,2 @@\\n-old = 1\\n+new = 2\\n+print(new)\\n':'',text:null};
 if(window.fail){window.fail=false;throw Error('temporary read failure');}
 if(window.hold){window.hold=false;return new Promise(resolve=>{window.release=()=>resolve(value);});}
 return value;
}};
function App(){const [cid,setCid]=useState('a'),[second,setSecond]=useState(false),[stop,setStop]=useState(false),[selection,setSelection]=useState<any>(null);
window.next=()=>setSecond(true);window.changeChat=id=>{setCid(id);setSelection(null);};window.stop=()=>setStop(true);window.reset=()=>{setCid('a');setSecond(false);setStop(false);setSelection(null);};
const first={id:'answer',role:'assistant',content:'已调整数据处理逻辑，并保留原代码备份。参考 docs/processing.md。',created_at:1795529940,meta:{turn_id:'first'},file_references:[reference,{...reference,path:record.path,name:record.name,references:[record.name]}],turn_files:[record,backup]};
return <StoreContext.Provider value={store}><div className="workbench" style={{height:'100vh',display:'flex',background:'var(--bg)',color:'var(--text)'}}>
<main className="conversation" style={{flex:1,minWidth:0}}><header className="file-toolbar"><strong>数据处理脚本</strong></header><div className="message-scroll"><div className="messages">
{cid==='a'?<><MessageBubble cid={cid} active={false} message={{id:'question',role:'user',content:'调整处理逻辑，并保留原代码备份。'}} onAction={no} onFile={no}/>
<AssistantTurn messages={stop?[{...first,content:'',tool_calls:[{id:'call',function:{name:'apply_patch',arguments:'{}'}}]}]:[first]} cid={cid} active={false} stopped={stop} onAction={no} onFile={path=>window.opened.push(path)} onHistory={(files,selected)=>setSelection({files,selected:selected||files[0].id})}/>
{second&&<><MessageBubble cid={cid} active={false} message={{id:'next',role:'user',content:'解释一下修改后的逻辑。'}} onAction={no} onFile={no}/><AssistantTurn messages={[{id:'second',role:'assistant',content:'新的处理顺序先检查输入，再执行转换。',meta:{turn_id:'second'}}]} cid={cid} active={false} onAction={no} onFile={no}/></>}</>:<p>另一条聊天</p>}
</div></div></main>{selection&&<aside className="workspace" style={{width:'46%',minWidth:230,display:'flex',flexDirection:'column'}}><header className="file-toolbar">文件记录</header><FileHistoryPanel key={cid} cid={cid} {...selection} select={selected=>setSelection({...selection,selected})} openFile={path=>window.opened.push(path)}/></aside>}</div></StoreContext.Provider>;
}createRoot(document.getElementById('root')).render(<App/>);
`);
    await build({root:data,configFile:false,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},build:{outDir:path.join(data,'dist')}});
    await writeFile(path.join(data,'run.cjs'),`
const {app,BrowserWindow,screen}=require('electron');const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
app.setPath('userData',path.join(__dirname,'profile'));app.commandLine.appendSwitch('disable-gpu');
const timer=setTimeout(()=>app.exit(1),40000);
app.whenReady().then(async()=>{
const native=process.env.COWORK_TURN_FILES_OFFSCREEN!=='1';
const w=new BrowserWindow({show:false,width:1360,height:800,useContentSize:true,webPreferences:{offscreen:!native,backgroundThrottling:false}});
const errors=[];w.webContents.on('console-message',d=>{if(d.level==='error')errors.push(d.message);});
const read=s=>w.webContents.executeJavaScript(s),delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const until=async s=>{for(let i=0;i<150;i++){if(await read(s))return;await delay(20);}throw Error(s+' '+errors.join(';'));};
const click=async s=>{await read(s+'.click()');await delay(30);};
await w.loadFile(path.join(__dirname,'dist/index.html'));await until('document.querySelectorAll(".chat-change-file").length===3');
assert.equal(await read('document.querySelectorAll(".chat-change-summary").length'),1);
await click('[...document.querySelectorAll(".chat-change-file")].find(el=>el.textContent.includes("processing.md"))');
assert.equal(await read('window.opened.length'),1);assert.equal(await read('Boolean(document.querySelector(".workspace"))'),false);
await click('document.querySelector(".chat-change-file")');await until('Boolean(document.querySelector(".diff-added"))');
assert.equal(await read('window.opened.length'),1);
await fs.writeFile(path.join(${JSON.stringify(root)},'unified-files.png'),(await w.webContents.capturePage({x:0,y:0,width:1360,height:430})).toPNG());
await read('window.next()');await until('document.body.textContent.includes("新的处理顺序")');
assert.equal(await read('document.querySelectorAll(".chat-change-summary").length'),1);
assert.equal(await read('document.querySelectorAll(".chat-change-file").length'),3);
await read('window.mode="purged"');await click('document.querySelectorAll(".chat-change-file")[1]');await until('document.body.textContent.includes("历史差异正文已按存储配额清理")');
assert.equal(await read('Boolean(document.querySelector(".diff-added"))'),false);
await read('window.mode="baseline_missing"');await click('document.querySelector(".chat-change-file")');await until('document.body.textContent.includes("缺少可信的执行前内容")');
await read('window.mode="available";window.fail=true');await click('document.querySelectorAll(".chat-change-file")[1]');await until('document.body.textContent.includes("历史记录读取失败")');
await click('[...document.querySelectorAll("button")].find(el=>el.textContent==="重试")');await until('Boolean(document.querySelector(".diff-added"))');
await read('window.hold=true');await click('document.querySelector(".chat-change-file")');await until('Boolean(window.release)');await read('window.changeChat("b")');await delay(50);await read('window.release()');await delay(50);
assert.equal(await read('Boolean(document.querySelector(".diff-added"))'),false);
await read('window.reset();window.stop()');await until('document.body.textContent.includes("已停止")');assert.equal(await read('document.querySelectorAll(".chat-change-file").length'),2);
await read('window.reset()');await delay(80);w.setSize(620,760);w.webContents.setZoomFactor(1.25);await delay(150);
await read('document.documentElement.dataset.theme="dark"');await delay(80);
assert.equal(await read('getComputedStyle(document.documentElement).getPropertyValue("--bg").trim()'),'#202020');
assert.equal(await read('document.documentElement.scrollWidth<=window.innerWidth'),true);
await read('document.querySelector(".chat-change-file").focus()');assert.equal(await read('document.activeElement.className'),'chat-change-file');
await fs.writeFile(path.join(${JSON.stringify(root)},'unified-files-dark-narrow.png'),(await w.webContents.capturePage()).toPNG());
await read('document.documentElement.dataset.theme="light"');await delay(50);assert.equal(await read('document.querySelectorAll(".chat-change-file").length'),3);
assert.deepEqual(errors,[]);clearTimeout(timer);console.log('TURN_FILES_OK',JSON.stringify({native,size:w.getContentSize(),displayScale:screen.getDisplayMatching(w.getBounds()).scaleFactor,zoom:w.webContents.getZoomFactor()}));app.exit(0);
}).catch(error=>{console.error(error.stack);app.exit(1);});
`);
    const env={...process.env,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(data,'run.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/TURN_FILES_OK/);console.log(output.match(/TURN_FILES_OK[^\r\n]*/)?.[0]);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
