// Run the production main/preload/renderer against an isolated real service.
// The only replacement makes its window offscreen; no product RPC is mocked.
const electron=require('electron'),Module=require('node:module');
const {app}=electron,fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const originalLoad=Module._load;
app.getVersion=()=>require('../package.json').version;
class HiddenWindow extends electron.BrowserWindow {
  constructor(options){super({...options,show:false,webPreferences:{...options.webPreferences,offscreen:true,backgroundThrottling:false}});}
  show(){} showInactive(){}
}
Module._load=function(name,...rest){return name==='electron'?{...electron,BrowserWindow:HiddenWindow}:originalLoad.call(this,name,...rest);};
app.commandLine.appendSwitch('disable-gpu');
const errors=[];let window;
const rpcTrace=[];
let slowDesign='';
const {Backend}=require('../electron/backend.cjs'),backendCall=Backend.prototype.call;
Backend.prototype.call=async function(method,p={}){
  try{const result=await backendCall.call(this,method,p);if(['view.save','draft.save','conversation.get'].includes(method))rpcTrace.push([method,p.conversation_id,p.revision,result.revision,result.view_revision]);if(method==='conversation.get'&&p.conversation_id===slowDesign)await new Promise(resolve=>setTimeout(resolve,300));return result;}
  catch(error){rpcTrace.push(['FAILED',method,p.conversation_id,p.revision]);throw error;}
};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const deadline=setTimeout(()=>{console.error('CREATION_TIMEOUT',errors);app.exit(1);},100000);
const read=async code=>{try{return await window.webContents.executeJavaScript(code);}catch(error){throw Error(code+'\n'+error.message);}};
const until=async code=>{const start=Date.now();while(Date.now()-start<15000){if(await read(code))return;await delay(40);}throw Error('Timed out '+code+' '+errors.join('\n'));};
const click=async text=>{const selector=`[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===${JSON.stringify(text)}&&!el.closest('[hidden]'))`;await until(`Boolean(${selector})`);await read(`${selector}.click()`);};
const rpc=(method,params={})=>read(`window.cowork.call(${JSON.stringify(method)},${JSON.stringify(params)})`);
const input=async text=>{await read(`(()=>{const el=document.querySelector('[aria-label="消息输入"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,${JSON.stringify(text)});el.dispatchEvent(new Event('input',{bubbles:true}));})()`);};
const snapshot=async name=>{
  const target=process.env.COWORK_CREATION_SCREENSHOTS;if(!target)return;
  window.webContents.invalidate();await delay(200);await fs.mkdir(target,{recursive:true});
  await fs.writeFile(path.join(target,name+'.png'),(await window.webContents.capturePage()).toPNG());
};
app.once('browser-window-created',(_event,win)=>{
  window=win;win.webContents.on('console-message',details=>{if(details.level==='error')errors.push(details.message);});
  win.webContents.once('did-finish-load',()=>void run().catch(async error=>{
    console.error(error.stack,errors,JSON.stringify(rpcTrace.slice(-70)));await snapshot('failure');
    // Shut down the isolated service even on assertion failure.
    const {Backend}=require('../electron/backend.cjs');
    try{const backend=new Backend({root:path.resolve(__dirname,'../..'),dataDir:process.env.COWORK_APP_DATA_DIR,publish:()=>{}});await backend.ready;await backend.stop();backend.detach();}catch(reason){console.error(reason);}
    app.exit(1);
  }));
});
async function run(){
  await until('Boolean(document.querySelector("[aria-label=消息输入]"))');
  await input('保留原对话输入');
  await read('document.querySelector("[aria-label=添加上下文]").click()');await click('模式');
  await until('document.querySelector(".composer-submenu")?.textContent.includes("创造模式") || [...document.querySelectorAll(".picker-row")].some(el=>el.textContent.includes("创造模式"))');
  await snapshot('creation-mode-menu');
  await read('[...document.querySelectorAll(".picker-row")].find(el=>el.textContent.includes("创造模式")).click()');
  await until('Boolean(document.querySelector(".creation-workbench"))');
  await until('Boolean(document.querySelector("[aria-label=消息输入]"))');
  const conversations=await rpc('conversation.list');
  const design=conversations.find(item=>item.meta.creation_session),cid=design.id,source=design.meta.creation_source_id;
  assert.equal((await rpc('conversation.get',{conversation_id:source})).draft.text,'保留原对话输入');
  const workspace=design.meta.workspace_dir;
  assert.ok((await fs.stat(path.join(workspace,'reference/sdk/client.d.ts'))).isFile());
  await click('选择界面区域');
  await read('(()=>{const el=document.querySelector("[data-creation-region=侧栏导航]");el.focus();el.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}));})()');
  await until('document.querySelector("[aria-label=修改范围]").value==="侧栏导航"');
  await click('AI 主题');await until('document.querySelector("[aria-label=修改范围]").value==="主题"');
  await snapshot('creation-workbench');
  await input('保留设计输入');
  await click('主题');await until('Boolean(document.querySelector(".theme-tile"))');
  await snapshot('creation-themes');
  await click('AI 设计');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留设计输入');
  await click('退出创造');await until('!document.querySelector(".creation-workbench")');
  await until('Boolean(document.querySelector("[aria-label=消息输入]"))');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留原对话输入');
  await read('document.querySelector("[aria-label=添加上下文]").click()');await click('模式');
  await read('[...document.querySelectorAll(".picker-row")].find(el=>el.textContent.includes("创造模式")).click()');
  await until('Boolean(document.querySelector(".creation-workbench"))');
  await until('Boolean(document.querySelector("[aria-label=消息输入]"))');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留设计输入');
  assert.equal(await read('document.querySelector("[aria-label=修改范围]").value'),'主题');
  // Publish an actual scoped candidate through the same repository used by tools.
  const {execFileSync}=require('node:child_process');
  execFileSync(path.resolve(__dirname,'../../.venv/Scripts/python.exe'),['-X','utf8','-B','-c',
    'import sys; from core.theme_service import ThemeRepository; from core.theme_tokens import default_design_tokens; ThemeRepository(sys.argv[1],preview_session=sys.argv[2]).write_preview(name="柔和阅读",overrides={"tokens":{"primary":"#2255aa"}},default_tokens=default_design_tokens(),session_id=sys.argv[2])',process.env.COWORK_APP_DATA_DIR,cid],{cwd:path.resolve(__dirname,'../..'),windowsHide:true});
  const originalTheme=(await rpc('themes.list')).active;
  await rpc('themes.appearance',{value:{mode:'light',reading:{}}});
  await until('document.querySelector(".creation-candidate")?.textContent.includes("柔和阅读")');
  assert.equal((await rpc('themes.list')).active,originalTheme);
  assert.equal((await rpc('themes.preview')).preview,null);
  await snapshot('creation-theme-candidate');
  await click('应用');await until('!document.querySelector(".creation-candidate")');
  assert.notEqual((await rpc('themes.list')).active,originalTheme);
  // Renderer reload restores the same independently persisted draft and scope.
  await until('window.cowork.call("view.get",{conversation_id:'+JSON.stringify(cid)+'}).then(value=>value.creationScope==="主题")');
  const reloaded=new Promise(resolve=>window.webContents.once('did-finish-load',resolve));
  window.webContents.send('cowork:events',[{event:'reload_requested'}]);await reloaded;
  await until('Boolean(document.querySelector(".creation-workbench [aria-label=消息输入]"))');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留设计输入');
  await fs.cp(path.resolve(__dirname,'../examples/workbench'),path.join(workspace,'interface'),{recursive:true});
  const manifestPath=path.join(workspace,'interface/manifest.json'),manifest=await fs.readFile(manifestPath,'utf8');
  await fs.writeFile(manifestPath,JSON.stringify({...JSON.parse(manifest),id:'INVALID'}));
  // Refresh through the ordinary appearance event, with a newly generated candidate on disk.
  await rpc('themes.appearance',{value:{mode:'light',reading:{}}});
  await click('预览界面候选');await until('document.querySelector(".creation-error")?.textContent.includes("插件标识")');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留设计输入');
  await fs.writeFile(manifestPath,manifest);
  await click('预览界面候选');await until('Boolean(document.querySelector(".creation-preview-content iframe"))');
  await until('!document.querySelector(".creation-preview-content .editor-loading")');
  await snapshot('creation-candidate');
  await click('应用');await until('document.querySelector(".creation-tools-footer").textContent.includes("已应用")');
  assert.ok((await rpc('plugins.inventory')).plugins.some(plugin=>plugin.id==='example.focus'&&plugin.enabled));
  await click('退出创造');await until('Boolean(document.querySelector(".host-creation-toggle"))');
  slowDesign=cid;
  await click('创造模式');await delay(100);
  assert.equal(await read('Boolean(document.querySelector(".creation-workbench"))'),false,'design actions must wait for session restoration');
  await until('Boolean(document.querySelector(".creation-workbench"))');slowDesign='';
  await click('恢复界面');await until('Boolean(document.querySelector(".modal"))');
  await read('[...document.querySelectorAll(".modal button")].find(el=>el.textContent.trim()==="恢复界面").click()');
  await until('document.querySelector(".creation-tools-footer").textContent.includes("界面已恢复")');
  assert.equal((await rpc('themes.list')).active,originalTheme);
  await click('插件');await until('document.querySelector(".creation-management").textContent.includes("拷问模式")');
  await snapshot('creation-plugins');
  await read('[...document.querySelectorAll(".plugin-card")].find(el=>el.textContent.includes("拷问模式")).querySelector("button").click()');
  await until('[...document.querySelectorAll(".plugin-card")].find(el=>el.textContent.includes("拷问模式"))?.textContent.includes("已停用")');
  assert.equal((await rpc('modes.list')).find(item=>item.mode==='grilling').enabled,false);
  await click('AI 设计');
  await rpc('themes.appearance',{value:{mode:'dark',reading:{}}});
  window.setMinimumSize(0,0);window.setSize(900,760);window.webContents.setZoomFactor(1.25);
  await until('document.documentElement.dataset.theme==="dark"');
  assert.ok(await read('document.querySelector(".creation-workbench").scrollWidth<=innerWidth+1'));
  await snapshot('creation-narrow-dark');
  window.setSize(1400,930);window.webContents.setZoomFactor(1);
  await click('退出创造');await until('!document.querySelector(".creation-workbench")');
  await until('Boolean(document.querySelector("[aria-label=消息输入]"))');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留原对话输入');
  assert.equal(errors.filter(error=>/workbench_render_failed|Uncaught|Maximum update/.test(error)).length,0,errors.join('\n'));
  clearTimeout(deadline);console.log('CREATION_MODE_OK');await rpc('app.quit');
}
require('../electron/main.cjs');
