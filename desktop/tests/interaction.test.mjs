import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');

test('questionnaire choices, custom text, retry, receipts and composer dismissal in Electron',{timeout:60000},async()=>{
  const root=path.resolve(desktop,'../.tmp/interaction-tests');await mkdir(root,{recursive:true});
  const data=await mkdtemp(path.join(root,'case-'));
  try {
    const source=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'index.html'),'<div id="root"></div><script type="module" src="/entry.tsx"></script>');
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useRef,useState} from 'react';import {createRoot} from 'react-dom/client';
import Interaction from ${source('Interaction.tsx')};import Composer from ${source('Composer.tsx')};
import {StoreContext} from ${source('host/context.tsx')};import {HostStore} from ${source('host/store.ts')};
import ${source('styles.css')};import ${source('composer.css')};
document.body.style.background='var(--bg)';
const store=new HostStore();window.replies=[];window.fail=true;window.sent=0;
window.cowork={call:async(method,p)=>{if(method!=='interaction.respond')throw Error(method);window.replies.push(p);if(window.fail){window.fail=false;throw Error('暂时无法提交，请重试');}return {resolved:true,response:{answers:p.value}};}};
const request={request_id:'question',kind:'questionnaire',title:'确认需求',questions:[{id:'choice',question:'选择方向',options:[{value:'one',label:'选项一'},{value:'two',label:'选项二'}]},{id:'text',question:'填写关键词',options:[]}]};
const models=[{id:'m1',model_name:'model-one',label:'模型一',display_name:'模型一',channel_id:'c',channel_display_name:'模型服务',configured:true},{id:'m2',model_name:'model-two',label:'模型二',display_name:'模型二',channel_id:'c',channel_display_name:'模型服务',configured:true}];
function App(){const [text,setText]=useState(''),[model,setModel]=useState('m1'),textarea=useRef(null);const no=async()=>{};return <StoreContext.Provider value={store}><main style={{padding:24,maxWidth:700,margin:'auto',background:'var(--bg)',color:'var(--text)'}}><Interaction request={request} conversationId="conversation" active done={()=>{}}/><Composer conversation={{id:'conversation',meta:{}}} conversations={[]} settings={{models,selected_model_id:model}} text={text} setText={setText} attachments={[]} setAttachments={()=>{}} skills={[]} active={false} sending={false} textarea={textarea} ensureConversation={async()=> 'conversation'} preferences={no} send={async()=>window.sent++} stop={no} attach={no} permission={no} selectModel={async id=>setModel(id)} route={no} browseLibrary={no} error={message=>{throw Error(message)}}/></main></StoreContext.Provider>;}createRoot(document.getElementById('root')).render(<App/>);
`);
    await build({root:data,configFile:false,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},build:{outDir:path.join(data,'dist')}});
    await writeFile(path.join(data,'run.cjs'),`
const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
app.setPath('userData',path.join(__dirname,'profile'));app.commandLine.appendSwitch('disable-gpu');
const timer=setTimeout(()=>app.exit(1),35000);
app.whenReady().then(async()=>{const w=new BrowserWindow({show:false,width:900,height:1000,webPreferences:{offscreen:true,backgroundThrottling:false}});
const errors=[];w.webContents.on('console-message',d=>{if(d.level==='error')errors.push(d.message);});
const read=s=>w.webContents.executeJavaScript(s);const until=async s=>{for(let i=0;i<150;i++){if(await read(s))return;await new Promise(r=>setTimeout(r,20));}throw Error(s+' '+errors.join(';'));};
const click=async s=>{await read(s+'.click()');await new Promise(r=>setTimeout(r,30));};
const input=async(s,value)=>{await read('(()=>{const el='+s+';Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(el,'+JSON.stringify(value)+');el.dispatchEvent(new Event("input",{bubbles:true}));})()');};
await w.loadFile(path.join(__dirname,'dist/index.html'));await until('Boolean(document.querySelector(".interaction"))');
assert.equal(await read('document.querySelector("input[type=radio]").checked'),true);
await click('document.querySelectorAll("input[type=radio]")[1]');assert.equal(await read('document.querySelector(".question-heading small").textContent'),'选项二');
await input('document.querySelector("input[placeholder=其他回答]")','two');await until('!document.querySelectorAll("input[type=radio]")[1].checked');
await click('document.querySelectorAll("input[type=radio]")[0]');
await click('document.querySelectorAll(".question-heading")[1]');await input('document.querySelector("input[placeholder=其他回答]")','大苏打撒');
await click('document.querySelector(".questionnaire-footer button")');await until('document.querySelector(".error-text")?.textContent.includes("重试")');
assert.equal(await read('document.querySelector("input[placeholder=其他回答]").value'),'大苏打撒');
assert.deepEqual(await read('window.replies[0].value'),{choice:{selected_options:['one']},text:{text:'大苏打撒'}});
await click('document.querySelector(".questionnaire-footer button")');await until('Boolean(document.querySelector(".interaction-receipt"))');
assert.match(await read('document.querySelector(".interaction-receipt").textContent'),/已提交 2 项回答/);
await read('document.querySelector(".interaction-receipt").open=true');assert.match(await read('document.querySelector(".interaction-receipt").textContent'),/选项一/);
await click('document.querySelector(".model-trigger")');await until('Boolean(document.querySelector(".model-menu"))');
assert.equal(await read('document.querySelector(".model-menu").textContent.includes("用途待")'),false);
await read('document.querySelector("textarea").dispatchEvent(new PointerEvent("pointerdown",{bubbles:true}));document.querySelector("textarea").focus()');await until('!document.querySelector(".model-menu")');
await read('document.querySelector("textarea").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');await until('window.sent===1');
await click('document.querySelector(".model-trigger")');await click('[...document.querySelectorAll(".model-menu .picker-row")].find(el=>el.textContent==="模型二")');await until('!document.querySelector(".model-menu")');
w.setSize(640,800);w.webContents.setZoomFactor(1.25);await read('document.documentElement.dataset.theme="dark"');
if(process.env.COWORK_INTERACTION_SCREENSHOTS){await new Promise(r=>setTimeout(r,250));await fs.mkdir(process.env.COWORK_INTERACTION_SCREENSHOTS,{recursive:true});await fs.writeFile(path.join(process.env.COWORK_INTERACTION_SCREENSHOTS,'questionnaire.png'),(await w.webContents.capturePage()).toPNG());}
assert.equal(errors.length,0,errors.join(';'));clearTimeout(timer);console.log('INTERACTION_OK');app.exit(0);
}).catch(e=>{console.error(e.stack);app.exit(1);});
`);
    const env={...process.env,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(data,'run.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',v=>output+=v);child.stderr.on('data',v=>output+=v);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/INTERACTION_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
