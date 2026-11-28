import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {spawn, spawnSync} from 'node:child_process';

test('built-in editor bridge accepts complete structured payloads only from its parent', async () => {
  const events = [], calls = [];
  let listener, bridge;
  const parent = { postMessage: event => events.push(event) };
  const window = { addEventListener: (_event, callback) => { listener = callback; }, coworkEditor: {
    load: (...args) => calls.push(['load', ...args]),
  } };
  vm.runInNewContext(fs.readFileSync('../web/editors/static/desktop-bridge.js', 'utf8'), { window, parent });
  window.connectDesktopBridge('deliverableEditorBridge', value => { bridge = value; });
  bridge.ready('sheet');
  assert.equal(events[0].method, 'ready');
  const send = (method, args, source = parent) => listener({ source, data: { channel: 'cowork-editor-host-v1', method, args } });
  const payload = {sheets: {sheet: {cellData: {0: {0: {v: '完整内容'.repeat(100000)}}}}}};
  await send('load', ['session', payload]);
  await send('load', ['foreign', {}], {});
  assert.deepEqual(calls, [['load', 'session', payload]]);
  bridge.payload('session', 'sheet', payload);
  assert.deepEqual(Array.from(events.at(-1).args), ['session', 'sheet', payload]);
  await send('constructor', []);
  assert.equal(events.at(-1).method, 'reportError');
});

test('shared bridge preserves terminal commands and their results', async () => {
  const events = [], writes = [];
  let listener;
  const parent = {postMessage: event => events.push(event)};
  const window = {addEventListener: (_event, callback) => {listener = callback;}, coworkTerminal: {
    write: (text, cursor) => {writes.push([text, cursor]); return cursor;},
    fit: () => ({cols: 80, rows: 24}),
  }};
  vm.runInNewContext(fs.readFileSync('../web/editors/static/desktop-bridge.js', 'utf8'), {window, parent});
  window.connectDesktopBridge('terminalBridge', () => {});
  for (const [method, args, id] of [['write', ['输出\r\n', 20], 'write-1'], ['fit', [], 'fit-1']]) {
    await listener({source: parent, data: {channel: 'cowork-editor-host-v1', method, args, id}});
  }
  assert.deepEqual(writes, [['输出\r\n', 20]]);
  assert.equal(events[0].method, 'commandResult');
  assert.equal(events[0].args[1], 20);
  assert.deepEqual(events[1].args[1], {cols: 80, rows: 24});
});

test('built editor pages use the native bridge without Qt scripts', () => {
  for (const name of ['docx', 'docx-review', 'sheet', 'html', 'terminal']) {
    const html = fs.readFileSync(`../web/editors/dist/${name}.html`, 'utf8');
    assert.match(html, /desktop-bridge\.js/);
    assert.doesNotMatch(html, /qrc:|qwebchannel/i);
  }
});

test('all four real editors export twice without navigating away or changing payload types', {skip:!process.env.COWORK_PYTHON, timeout:60000}, async () => {
  const root=path.resolve('..'),tempRoot=path.join(root,'.tmp/editor-protocol-tests');
  fs.mkdirSync(tempRoot,{recursive:true});const data=fs.mkdtempSync(path.join(tempRoot,'case-'));
  try {
    const generated=spawnSync(process.env.COWORK_PYTHON,['-X','utf8','-B','-c',`
import base64,json,sys
from pathlib import Path
from docx import Document
from core.docx_review import load_review
from core.deliverable_editing import rows_to_univer_snapshot
folder=Path(sys.argv[1]);document=Document();document.add_paragraph('协议往返中文');document.save(folder/'source.docx')
raw=(folder/'source.docx').read_bytes()
print(json.dumps({'docx':base64.b64encode(raw).decode(),'docx_review':load_review(raw),'sheet':rows_to_univer_snapshot([['中文','2']]),'html':'<!doctype html><html><body><p>完整载荷</p><!--'+'payload-'*30000+'--></body></html>'},ensure_ascii=False))
`,data],{cwd:root,encoding:'utf8',windowsHide:true,env:{...process.env,TEMP:data,TMP:data}});
    assert.equal(generated.status,0,generated.stderr);fs.writeFileSync(path.join(data,'inputs.json'),generated.stdout);
    fs.writeFileSync(path.join(data,'fixture.cjs'),`
const {app,BrowserWindow,protocol,net}=require('electron'),fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{pathToFileURL}=require('node:url');
const root=${JSON.stringify(root)},data=__dirname,delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
protocol.registerSchemesAsPrivileged([{scheme:'cowork-editor',privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true}}]);
const deadline=setTimeout(()=>app.exit(2),50000);
app.whenReady().then(async()=>{
  protocol.handle('cowork-editor',request=>net.fetch(pathToFileURL(path.join(root,'web/editors/dist',new URL(request.url).pathname.slice(1))).href));
  const window=new BrowserWindow({show:false,webPreferences:{offscreen:true,backgroundThrottling:false}}),read=code=>window.webContents.executeJavaScript(code);
  await window.loadURL('data:text/html,'+encodeURIComponent('<iframe style="width:100vw;height:100vh"></iframe><script>window.events=[];window.addEventListener("message",e=>{if(e.source===document.querySelector("iframe").contentWindow)events.push(e.data);});</script>'));
  async function until(code){const start=Date.now();while(Date.now()-start<10000){if(await read(code))return;await delay(20);}throw Error('timeout '+code+' '+JSON.stringify(await read('events')));}
  async function send(method,args=[]){await read('document.querySelector("iframe").contentWindow.postMessage('+JSON.stringify({channel:'cowork-editor-host-v1',method,args})+',"*")');}
  const inputs=JSON.parse(await fs.readFile(path.join(data,'inputs.json'),'utf8'));
  for(const [kind,payload] of Object.entries(inputs)){
    const page=kind==='docx_review'?'docx-review':kind;
    await read('events=[];document.querySelector("iframe").src='+JSON.stringify('cowork-editor://builtin/'+page+'.html'));
    await until('events.some(e=>e.method==="ready")');
    await send('load',[kind,payload]);await until('events.some(e=>e.method==="editorLoaded")');
    for(let count=1;count<=2;count++){
      await send('requestSave');await until('events.filter(e=>e.method==="payload").length==='+count);
      const result=await read('events.filter(e=>e.method==="payload").at(-1).args');
      assert.equal(result[0],kind);assert.equal(result[1],kind);
      assert.equal(typeof result[2],kind==='docx'||kind==='html'?'string':'object');
      if(kind==='html')assert(result[2].length>192*1024);
      await delay(50);assert(window.webContents.mainFrame.frames.some(frame=>frame.url==='cowork-editor://builtin/'+page+'.html'));
    }
    assert.equal(await read('events.some(e=>e.method==="reportError")'),false);
  }
  clearTimeout(deadline);window.destroy();console.log('EDITOR_ROUNDTRIPS_OK');app.exit(0);
}).catch(error=>{console.error(error);clearTimeout(deadline);app.exit(1);});
`);
    const env={...process.env,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(createRequire(import.meta.url)('electron'),[path.join(data,'fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/EDITOR_ROUNDTRIPS_OK/);
  } finally {fs.rmSync(data,{recursive:true,force:true,maxRetries:3});}
});
