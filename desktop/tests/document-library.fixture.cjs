// Production shell, preload, FileHost, view persistence and filesystem. Only
// remote library responses are controlled to reproduce slow/late requests.
const electron=require('electron'),Module=require('node:module');
const {app}=electron,fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const originalLoad=Module._load;app.getVersion=()=>require('../package.json').version;
class HiddenWindow extends electron.BrowserWindow{constructor(options){super({...options,show:false,webPreferences:{...options.webPreferences,offscreen:true,backgroundThrottling:false}});}show(){}showInactive(){}}
Module._load=function(name,...rest){return name==='electron'?{...electron,BrowserWindow:HiddenWindow}:originalLoad.call(this,name,...rest);};
app.commandLine.appendSwitch('disable-gpu');
const {Backend}=require('../electron/backend.cjs'),backendCall=Backend.prototype.call;
let holdRead=false,readSequence=0,createDelay=0;const libraryRequests=[];const jobs=new Map(),cancelled=[],errors=[];
const pdfPath=path.join(process.env.COWORK_APP_DATA_DIR,'remote.pdf');
Backend.prototype.call=async function(method,p={}){
  if(method==='workspace.create'&&createDelay){const result=await backendCall.call(this,method,p);await delay(createDelay);return result;}
  if(method==='library.status')return {active:'weknora',sources:Object.fromEntries(['weknora','tencent-docs','lexiang'].map(source=>[source,{source,user_id:'fixture'}]))};
  if(method==='library.catalog'&&p.source!=='local')return {mine:[{id:'own',name:'个人资料'}],shared:[{id:'own',name:'个人资料',organization_name:'产品团队'},{id:'shared',name:'团队资料',organization_name:'产品团队'},{id:'shared',name:'团队资料',organization_name:'研究团队'}]};
  if(method==='library.children'&&p.source!=='local'){
    assert.ok(p.collection,'file pagination requires collection');libraryRequests.push(p);
    const files=[{id:'document',title:'培训说明.pdf',file_type:'pdf'},{id:'text',title:'工作说明',file_type:'md'}];
    return p.source==='weknora'?{items:files,has_more:false,total:2}:[files,false];
  }
  if(method==='library.read'&&p.source!=='local'){
    if(p.item==='text')return {text:'# 工作说明\n\n正文内容可见',format:'markdown'};
    const id='remote-'+(++readSequence);jobs.set(id,{id,status:holdRead?'running':'completed',result:{kind:'library_preview'}});return {job_id:id};
  }
  if(method==='jobs.get'&&jobs.has(p.id))return jobs.get(p.id);
  if(method==='jobs.cancel'&&jobs.has(p.id)){cancelled.push(p.id);return {...jobs.get(p.id),status:'cancelled'};}
  if(method==='library.read_result')return {path:pdfPath,payload:{kind:'preview',preview_kind:'pdf',read_only:true}};
  return backendCall.call(this,method,p);
};
let window;const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const read=code=>window.webContents.executeJavaScript(code);
const until=async code=>{const start=Date.now();while(Date.now()-start<15000){if(await read(code))return;await delay(40);}throw Error('Timed out '+code+' '+errors.join('\n'));};
const rpc=(method,params={})=>read(`window.cowork.call(${JSON.stringify(method)},${JSON.stringify(params)})`);
const click=async text=>{const selector=`[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===${JSON.stringify(text)}&&!el.closest('[hidden]'))`;await until(`Boolean(${selector})`);await read(`${selector}.click()`);};
const input=async(selector,text)=>read(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(text)});el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const frame=()=>window.webContents.mainFrame.framesInSubtree.find(item=>item.url.startsWith('cowork-plugin:'));
const readyEditor=async()=>{await until('Boolean(document.querySelector(".plugin-surface iframe"))');const start=Date.now();while(Date.now()-start<10000){if(frame()&&await frame().executeJavaScript('Boolean(document.querySelector("#text"))&&document.querySelector("#status").textContent!=="正在加载…"'))return;await delay(40);}throw Error('Writing frame unavailable');};
const snapshot=async name=>{if(!process.env.COWORK_FIX_SCREENSHOTS)return;await fs.mkdir(process.env.COWORK_FIX_SCREENSHOTS,{recursive:true});await delay(160);await fs.writeFile(path.join(process.env.COWORK_FIX_SCREENSHOTS,name+'.png'),(await window.webContents.capturePage()).toPNG());};
const deadline=setTimeout(()=>{console.error('DOCUMENT_TIMEOUT',errors);app.exit(1);},80000);
app.once('browser-window-created',(_event,win)=>{window=win;win.webContents.on('console-message',details=>{if(details.level==='error')errors.push(details.message);});win.webContents.once('did-finish-load',()=>void run().catch(async error=>{console.error(error.stack,errors);await snapshot('failure');try{const backend=new Backend({root:path.resolve(__dirname,'../..'),dataDir:process.env.COWORK_APP_DATA_DIR,publish:()=>{}});await backend.ready;await backend.stop();backend.detach();}catch{}app.exit(1);}));});
async function run(){
  await until('Boolean(document.querySelector("[aria-label=消息输入]"))');await read('document.querySelector(".chat-header-tools [aria-label=工作区]").click()');await click('新增页面');await readyEditor();
  const cid=(await rpc('conversation.list'))[0].id;
  assert.equal((await rpc('workspace.list',{conversation_id:cid})).files.length,0);
  assert.equal(await frame().executeJavaScript('document.querySelector("#text").hidden'),false);
  await frame().executeJavaScript('dispatchEvent(new KeyboardEvent("keydown",{key:"s",ctrlKey:true}));');await delay(100);
  assert.match(await frame().executeJavaScript('document.querySelector("#status").textContent'),/空白草稿/);
  assert.equal((await rpc('workspace.list',{conversation_id:cid})).files.length,0);
  await snapshot('writing-empty');
  await read('[...document.querySelectorAll(".workspace-tabs .tab-close")].find(el=>el.getAttribute("aria-label").startsWith("关闭 写作")).click()');
  await until('!document.querySelector(".draft-file-name")');
  assert.equal((await rpc('workspace.list',{conversation_id:cid})).files.length,0);
  await click('新增页面');await readyEditor();
  await input('[aria-label=文稿文件名]','工作笔记.md');
  await frame().executeJavaScript('document.querySelector("#text").value="# 工作笔记\\n\\n整理本周资料，记录下一步计划。";document.querySelector("#text").dispatchEvent(new Event("input"));');
  await until('Boolean(document.querySelector(".file-toolbar .primary"))');await delay(700);
  const before=await rpc('view.get',{conversation_id:cid});assert.ok(JSON.stringify(before).includes('下一步计划'));
  assert.equal((await rpc('workspace.list',{conversation_id:cid})).files.length,0);
  window.webContents.reload();await until('Boolean(document.querySelector("[aria-label=消息输入]"))');await readyEditor();
  assert.match(await frame().executeJavaScript('document.querySelector("#text").value'),/下一步计划/);
  assert.equal(await frame().executeJavaScript('document.querySelector("#text").hidden'),false);
  await snapshot('writing-draft');
  createDelay=500;await click('保存');
  await frame().executeJavaScript('document.querySelector("#text").value+="\\n保存期间继续输入";document.querySelector("#text").dispatchEvent(new Event("input"));');
  await until('!document.querySelector(".draft-file-name")');await readyEditor();createDelay=0;
  const files=(await rpc('workspace.list',{conversation_id:cid})).files;assert.equal(files.length,1);assert.equal(files[0].name,'工作笔记.md');
  assert.match(await fs.readFile(files[0].path,'utf8'),/下一步计划/);assert.equal(await frame().executeJavaScript('document.querySelector("#text").hidden'),false);
  assert.match(await frame().executeJavaScript('document.querySelector("#text").value'),/保存期间继续输入/);
  assert.ok(await read('Boolean(document.querySelector(".file-toolbar .primary"))'));
  await frame().executeJavaScript('dispatchEvent(new KeyboardEvent("keydown",{key:"s",ctrlKey:true}));');await until('!document.querySelector(".file-toolbar .primary")');
  assert.match(await fs.readFile(files[0].path,'utf8'),/保存期间继续输入/);
  const external=path.join(process.env.COWORK_APP_DATA_DIR,'outside.md');await fs.writeFile(external,'工作目录外的正文');
  const opened=await rpc('file.open',{conversation_id:cid,path:external});assert.match(opened.payload.url,/^cowork-local:/);assert.equal(opened.payload.content,'工作目录外的正文');
  const stream='BT /F1 18 Tf 35 150 Td (Library preview content) Tj ET';
  const objects=['<</Type/Catalog/Pages 2 0 R>>','<</Type/Pages/Kids[3 0 R]/Count 1>>','<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 240]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>','<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>',`<</Length ${stream.length}>>\nstream\n${stream}\nendstream`];
  let pdf='%PDF-1.4\n';const offsets=[0];for(const [i,obj] of objects.entries()){offsets.push(Buffer.byteLength(pdf));pdf+=`${i+1} 0 obj\n${obj}\nendobj\n`;}
  const xref=Buffer.byteLength(pdf);pdf+=`xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(offset=>String(offset).padStart(10,'0')+' 00000 n \n').join('')}trailer\n<</Size 6/Root 1 0 R>>\nstartxref\n${xref}\n%%EOF`;
  await fs.writeFile(pdfPath,pdf);
  await click('资料库');await until('Boolean(document.querySelector("[aria-label=选择资料空间]"))');
  await until('document.querySelector(".library-heading")?.textContent.includes("公共资料")');
  const imported=await rpc('library.import',{source:'local',collection:'public',paths:[external]});await until(`window.cowork.call('jobs.get',{id:${JSON.stringify(imported.job_id)}}).then(job=>job.status==='completed')`);await until('document.querySelector("[aria-label=刷新资料]").disabled===false');await read('document.querySelector("[aria-label=刷新资料]").click()');
  await until('document.querySelector(".library-list-scroll")?.textContent.includes("outside.md")');await snapshot('library-public');
  await click('outside.md');await until('document.querySelector(".library-document-content")?.textContent.includes("工作目录外的正文")');
  await click('返回列表');await click('项目资料');await until('document.querySelector(".library-heading")?.textContent.includes("项目资料")');
  assert.equal(await read('Boolean(document.querySelector(".library-load-more"))'),false);
  const source=async name=>{await read(`(()=>{const el=document.querySelector('[aria-label=选择资料空间]');el.value=${JSON.stringify(name)};el.dispatchEvent(new Event('change',{bubbles:true}));})()`);await until('document.querySelectorAll(".library-collections .library-collection-group button").length===2');};
  await source('weknora');assert.equal(await read('document.querySelectorAll(".library-row").length'),2);await snapshot('library-grouped');
  const collection=async name=>{await click('全部资料空间');await until('!document.querySelector(".library-list-actions")');await read(`[...document.querySelectorAll('.library-item-name')].find(el=>el.textContent.startsWith(${JSON.stringify(name)})).click()`);await until('document.querySelectorAll(".library-row").length===2&&Boolean(document.querySelector(".library-list-actions"))');};
  await click('产品团队');await until('document.querySelectorAll(".library-row").length===2');
  assert.equal(await read('Boolean(document.querySelector(".library-load-more"))'),false);
  await collection('个人资料');holdRead=true;await click('培训说明.pdf');await until('document.querySelector(".library-document-content")?.textContent.includes("正在读取")');
  assert.equal(await read('[...document.querySelectorAll(".library-collections button")].some(el=>el.disabled)'),false);
  await collection('团队资料');await until('!document.querySelector(".library-document")');await delay(100);assert.equal(cancelled.length,1);
  holdRead=false;await click('培训说明.pdf');await until('Boolean(document.querySelector(".library-document iframe"))');
  assert.match(await read('document.querySelector(".library-document iframe").src'),/^cowork-local:/);
  await delay(800);await snapshot('library-pdf');
  await click('返回列表');await click('工作说明');await until('document.querySelector(".library-document-content")?.textContent.includes("正文内容可见")');await snapshot('library-document');
  for(const name of ['tencent-docs','lexiang']){await source(name);await collection('团队资料');await click('工作说明');await until('document.querySelector(".library-document-content")?.textContent.includes("正文内容可见")');}
  window.setMinimumSize(0,0);window.setSize(980,760);window.webContents.setZoomFactor(1.25);await rpc('themes.appearance',{value:{mode:'dark',reading:{}}});await until('document.documentElement.dataset.theme==="dark"');await snapshot('library-narrow-dark');
  assert.equal(errors.filter(error=>/workbench_render_failed|Uncaught|Maximum update|same key/.test(error)).length,0,errors.join('\n'));
  clearTimeout(deadline);console.log('DOCUMENT_LIBRARY_OK');await rpc('app.quit');
}
require('../electron/main.cjs');
