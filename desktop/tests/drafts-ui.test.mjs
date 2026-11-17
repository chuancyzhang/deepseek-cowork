import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');

test('real workbench keeps input locally across new chat, switching and reload', {skip:!process.env.COWORK_PYTHON,timeout:90000},async()=>{
  const root=path.resolve(desktop,'../.tmp/nov17-draft-path/ui');await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  const fixture=String.raw`
const {app,screen}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {Backend}=require(${JSON.stringify(path.join(desktop,'electron/backend.cjs'))});
const original=Backend.prototype.call;let window,backend,saves=0;
Backend.prototype.call=async function(method,p){backend=this;if(method==='draft.save'||method==='draft.get'){saves++;throw Error('输入不应发起后台草稿同步');}return original.call(this,method,p);};
app.getVersion=()=>${JSON.stringify(JSON.parse(await (await import('node:fs/promises')).readFile(path.join(desktop,'package.json'),'utf8')).version)};
app.on('browser-window-created',(_e,value)=>window=value);
require(${JSON.stringify(path.join(desktop,'electron/main.cjs'))});
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const timer=setTimeout(()=>{console.error('draft UI timeout');app.exit(1);},80000);
app.whenReady().then(async()=>{
  while(!window)await delay(20);
  const read=code=>window.webContents.executeJavaScript(code);
  const until=async(code)=>{for(let i=0;i<450;i++){try{if(await read(code))return;}catch{}await delay(40);}throw Error('Timed out: '+code+' '+await read('document.body.textContent'));};
  const text=async value=>read('(()=>{const e=document.querySelector(\'textarea[aria-label="消息输入"]\');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set.call(e,'+JSON.stringify(value)+');e.dispatchEvent(new Event("input",{bubbles:true}));})()');
  const reload=async()=>{await new Promise(resolve=>{window.webContents.once('did-finish-load',resolve);window.webContents.reload();});await until('!!document.querySelector(\'textarea[aria-label="消息输入"]\')');};
  await until('!!document.querySelector(".new-chat")');
  if(process.env.COWORK_INPUT_RESTART){
    await until('document.querySelector(\'textarea[aria-label="消息输入"]\')?.value.includes("当前未发送")');
    const boot=await original.call(backend,'app.bootstrap',{});
    const restored=await read('JSON.parse(localStorage.getItem('+JSON.stringify('cowork-pending-draft:'+boot.last_conversation)+')).value');
    assert.equal(restored.attachments.length,1);assert.equal(await fs.readFile(restored.attachments[0].path,'utf8'),'input carry fixture');
    assert.equal(saves,0);assert.equal(await read('document.querySelectorAll(".draft-recovery").length'),0);
    console.log('DRAFT_RESTART_OK');clearTimeout(timer);
    await read('window.cowork.call("app.quit")').catch(error=>{if(!String(error).includes('disposed'))throw error;});return;
  }
  await read('document.querySelector(".new-chat").click()');
  await until('!!document.querySelector(".conversation-toolbar") || !!document.querySelector(".composer")');
  await delay(400);
  let bootstrap=await original.call(backend,'app.bootstrap',{});const cid=bootstrap.last_conversation;assert.ok(cid);
  await original.call(backend,'conversation.rename',{conversation_id:cid,title:'已有聊天'});
  await original.call(backend,'draft.save',{conversation_id:cid,value:{text:'旧版后台内容',attachments:[]}});
  await reload();
  const content='当前未发送的要求\n保留原有文件，并核对文档中的链接。';
  await text(content);
  const attachment=await original.call(backend,'workspace.attach_data',{conversation_id:cid,name:'材料.txt',base64:Buffer.from('input carry fixture').toString('base64')});
  await read('(()=>{const key='+JSON.stringify('cowork-pending-draft:'+cid)+';const entry=JSON.parse(localStorage.getItem(key));entry.value.attachments=['+JSON.stringify(attachment)+'];localStorage.setItem(key,JSON.stringify(entry));})()');
  await reload();
  await until('document.querySelector(\'textarea[aria-label="消息输入"]\').value.includes("当前未发送")');
  assert.equal(await read('document.querySelectorAll(".draft-recovery").length'),0);
  await read('document.querySelector(".new-chat").click()');await delay(600);
  bootstrap=await original.call(backend,'app.bootstrap',{});const next=bootstrap.last_conversation;assert.notEqual(next,cid);
  assert.equal(await read('document.querySelector(\'textarea[aria-label="消息输入"]\').value'),content);
  let local=await read('JSON.parse(localStorage.getItem('+JSON.stringify('cowork-pending-draft:'+next)+')).value');
  assert.equal(local.attachments.length,1);assert.notEqual(local.attachments[0].path,attachment.path);
  assert.equal(await fs.readFile(local.attachments[0].path,'utf8'),'input carry fixture');
  await original.call(backend,'file.describe',{conversation_id:next,path:local.attachments[0].path});
  assert.equal(await read('JSON.parse(localStorage.getItem('+JSON.stringify('cowork-pending-draft:'+cid)+')).value.text'),'');
  // Clicking new chat on an unsent blank chat keeps the same input and target.
  await read('document.querySelector(".new-chat").click()');await delay(200);
  assert.equal((await original.call(backend,'app.bootstrap',{})).last_conversation,next);
  await read('[...document.querySelectorAll(".history-select")].find(e=>e.textContent.includes("已有聊天")).click()');
  await until('document.querySelector(\'textarea[aria-label="消息输入"]\').value===""');
  await text('原聊天的另一项要求');
  await until('[...document.querySelectorAll(".history-select")].some(e=>e.textContent.includes("当前未发送"))');
  await read('[...document.querySelectorAll(".history-select")].find(e=>e.textContent.includes("当前未发送")).click()');
  await until('document.querySelector(\'textarea[aria-label="消息输入"]\').value.includes("当前未发送")');
  for(let i=0;i<2;i++){
    await reload();await until('document.querySelector(\'textarea[aria-label="消息输入"]\').value.includes("当前未发送")');
    assert.equal(await read('document.querySelectorAll(".draft-recovery").length'),0);
  }
  assert.equal(await read('JSON.parse(localStorage.getItem('+JSON.stringify('cowork-pending-draft:'+cid)+')).value.text'),'原聊天的另一项要求');
  assert.equal(saves,0,'typing, navigation and reload perform no draft RPC');
  if(process.env.COWORK_DRAFT_SCREENSHOTS){await fs.mkdir(process.env.COWORK_DRAFT_SCREENSHOTS,{recursive:true});await fs.writeFile(path.join(process.env.COWORK_DRAFT_SCREENSHOTS,'local-input.png'),(await window.webContents.capturePage()).toPNG());}
  window.setSize(1000,800);window.webContents.setZoomFactor(1.25);
  await read('window.cowork.call("settings.theme",{theme:"dark"})');await delay(400);
  assert.ok(await read('document.documentElement.scrollWidth<=innerWidth+1'),'small scaled window has no horizontal overflow');
  assert.equal(await read('document.querySelectorAll(".draft-recovery").length'),0);
  if(process.env.COWORK_DRAFT_SCREENSHOTS)await fs.writeFile(path.join(process.env.COWORK_DRAFT_SCREENSHOTS,'local-input-dark.png'),(await window.webContents.capturePage()).toPNG());
  console.log('DRAFT_UI_OK '+JSON.stringify({scale:screen.getPrimaryDisplay().scaleFactor,zoom:window.webContents.getZoomFactor(),size:window.getSize()}));
  clearTimeout(timer);await read('window.cowork.call("app.quit")').catch(error=>{if(!String(error).includes('disposed'))throw error;});
}).catch(async error=>{clearTimeout(timer);console.error(error);try{await original.call(backend,'app.shutdown',{});}catch{}app.exit(1);});
`;
  const script=path.join(data,'fixture.cjs');await writeFile(script,fixture);
  try{
    const env={...process.env,COWORK_APP_DATA_DIR:data,TEMP:data,TMP:data,APPDATA:data,LOCALAPPDATA:data};delete env.ELECTRON_RUN_AS_NODE;
    for(const restart of [false,true]){
      const child=spawn(require('electron'),[script],{env:{...env,...(restart?{COWORK_INPUT_RESTART:'1'}:{})},windowsHide:true,stdio:['ignore','pipe','pipe']});let output='';
      child.stdout.on('data',b=>output+=b);child.stderr.on('data',b=>output+=b);
      const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
      assert.equal(code,0,output);assert.match(output,restart?/DRAFT_RESTART_OK/:/DRAFT_UI_OK/);
      console.log(output.match(/DRAFT_(?:UI|RESTART)_OK[^\r\n]*/)?.[0]);
    }
  }finally{await rm(data,{recursive:true,force:true,maxRetries:6,retryDelay:300});}
});
