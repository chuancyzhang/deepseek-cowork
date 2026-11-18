const electron=require('electron'),Module=require('node:module');
const {app,Menu,screen}=electron,fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const originalLoad=Module._load;
let selectedFolder;
const dialog=Object.create(electron.dialog);
dialog.showOpenDialog=(...args)=>selectedFolder
  ? Promise.resolve({canceled:false,filePaths:[selectedFolder]}) : electron.dialog.showOpenDialog(...args);
app.getVersion=()=>require('../package.json').version;
class HiddenWindow extends electron.BrowserWindow {
  constructor(options){super({...options,show:false,webPreferences:{...options.webPreferences,offscreen:!process.env.COWORK_NAVIGATION_NATIVE,backgroundThrottling:false}});}
  show(){if(process.env.COWORK_NAVIGATION_NATIVE)super.showInactive();} showInactive(){this.show();}
}
Module._load=function(name,...rest){return name==='electron'?{...electron,dialog,BrowserWindow:HiddenWindow}:originalLoad.call(this,name,...rest);};
app.commandLine.appendSwitch('disable-gpu');
const {Backend}=require('../electron/backend.cjs'),originalCall=Backend.prototype.call;
let window,backend,slowSearch,fastSearch,searchFailure=false,taskReadRelease,stopFailure=false,browserHost;
const errors=[],runs=new Map(),stops=[],snapshots=new Map();
const {BrowserHost}=require('../electron/browser.cjs'),originalShow=BrowserHost.prototype.show;
BrowserHost.prototype.show=function(params){browserHost=this;return originalShow.call(this,params);};
Backend.prototype.call=async function(method,p={}){
  backend=this;
  if(method==='conversation.search'){
    if(p.query==='旧查询')return new Promise(resolve=>slowSearch=resolve);
    if(p.query==='新查询')return new Promise(resolve=>fastSearch=resolve);
    if(p.query==='失败'&&searchFailure)throw Error('测试搜索连接失败');
    if(p.query==='多结果')return Array.from({length:65},(_,index)=>({id:'search-'+index,title:'匹配聊天 '+index,meta:{}}));
  }
  if(method==='conversation.stop'&&runs.has(p.conversation_id)){
    if(stopFailure)throw Error('测试停止失败，任务仍在运行');
    stops.push(p);const run=runs.get(p.conversation_id);assert.equal(p.run_id,run.id);
    const finished={...run,status:'completed',updated_at:Date.now()/1000};runs.set(p.conversation_id,finished);
    window.webContents.send('cowork:events',[{event:'run_finished',conversation_id:p.conversation_id,run:finished}]);
    return {stopped:true,released:true};
  }
  const value=await originalCall.call(this,method,p);
  if(method==='conversation.get'){
    const result={...value,...snapshots.get(p.conversation_id),...(runs.has(p.conversation_id)?{run:runs.get(p.conversation_id)}:{})};
    if(taskReadRelease===p.conversation_id)return new Promise(resolve=>taskReadRelease=()=>resolve(result));
    return result;
  }
  return value;
};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const deadline=setTimeout(()=>{console.error('NAVIGATION_TIMEOUT',errors);app.exit(1);},80000);
const read=code=>window.webContents.executeJavaScript(code);
const until=async code=>{const start=Date.now();while(Date.now()-start<12000){if(await read(code))return;await delay(35);}throw Error('Timed out '+code+' '+await read('document.body.innerText'));};
const wait=async predicate=>{for(let i=0;i<350;i++){if(predicate())return;await delay(30);}throw Error('Fixture event timeout');};
const rpc=(method,params={})=>read(`window.cowork.call(${JSON.stringify(method)},${JSON.stringify(params)})`);
const command=id=>Menu.getApplicationMenu().getMenuItemById(id).click();
const fill=(selector,value)=>read(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.focus();Object.getOwnPropertyDescriptor(el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const snap=async name=>{const output=process.env.COWORK_NAVIGATION_SCREENSHOTS;if(!output)return;await fs.mkdir(output,{recursive:true});window.webContents.invalidate();await delay(120);await fs.writeFile(path.join(output,name+'.png'),(await window.webContents.capturePage()).toPNG());};
const clickText=(selector,text)=>read('Array.from(document.querySelectorAll('+JSON.stringify(selector)+')).find(button=>button.textContent.trim()==='+JSON.stringify(text)+').click()');
const openRow=(label)=>read('document.querySelector('+JSON.stringify('[data-sidebar-more][title="'+label+'"]')+').click()');
async function dragSidebar(selector, label) {
  const point = (query,text) => read('(()=>{const e=[...document.querySelectorAll('+JSON.stringify(query)+')].find(element=>element.textContent.trim()==='+JSON.stringify(text)+');e.scrollIntoView({block:"nearest"});const r=e.getBoundingClientRect();return {x:Math.floor(r.left+r.width/2),y:Math.floor(r.top+r.height/2)};})()');
  const source=await point(selector,label), target=await point('[data-sidebar-group] > button','产品研发');
  const debuggerClient=window.webContents.debugger;
  debuggerClient.attach('1.3');
  const input=(type,point,button,buttons)=>debuggerClient.sendCommand('Input.dispatchMouseEvent',{type,...point,button,buttons,clickCount:1});
  try {
    await input('mouseMoved',source,'none',0);
    await input('mousePressed',source,'left',1);
    await until('!!document.querySelector(".sidebar-drag-preview")');
    await input('mouseMoved',target,'none',1);
    await until('!!document.querySelector("[data-drop-target]")');
    await input('mouseReleased',target,'left',0);
    await until('!document.querySelector(".sidebar-drag-preview")');
  } finally {debuggerClient.detach();}
}
async function checkSidebar() {
  await until('document.querySelectorAll(".brand .navigation-tools button").length===2');
  command('sidebar');await until('document.querySelectorAll(".app-navigation .navigation-tools button").length===2');
  command('sidebar');await until('document.querySelectorAll(".brand .navigation-tools button").length===2');
  command('add-project');await until('document.activeElement===document.querySelector(".project-dialog input")');
  await fill('.project-dialog input','cross-platform-cowork');await snap('new-project');
  await clickText('.project-dialog button','保存');
  await until('!!document.querySelector("[title=\\"cross-platform-cowork 项目操作\\"]")');
  const root=(await rpc('settings.get')).project_workspace_root;
  assert.equal((await fs.stat(path.join(root,'cross-platform-cowork'))).isDirectory(),true);
  command('add-project');await until('document.activeElement===document.querySelector(".project-dialog input")');
  selectedFolder=path.join(process.env.COWORK_APP_DATA_DIR,'客户资料');
  await fs.mkdir(selectedFolder);
  await clickText('.project-dialog button','关联本机文件夹…');
  await until('document.querySelector(".project-dialog input")?.value==="客户资料"');
  await clickText('.project-dialog button','保存');
  await until('!!document.querySelector("[title=\\"客户资料 项目操作\\"]")');
  const projects=await rpc('project.list');
  assert.equal(projects.find(project=>project.name==='客户资料').path,selectedFolder);
  selectedFolder=undefined;

  await openRow('cross-platform-cowork 项目操作');
  await clickText('.sidebar-context-menu button','移到分组');
  await clickText('.sidebar-group-menu button','新建分组…');
  await until('document.activeElement===document.querySelector(".sidebar-group-form input")');
  assert.equal(await read('document.querySelector("dialog[open]")===null'),true);
  await fill('.sidebar-group-form input','产品研发');
  await read('document.querySelector(".sidebar-group-form").requestSubmit()');
  await until('document.querySelector(".sidebar-user-group")?.textContent.includes("cross-platform-cowork")');
  await dragSidebar('.project-heading > button:first-child','客户资料');
  await until('document.querySelector(".sidebar-user-group")?.textContent.includes("客户资料")');
  await dragSidebar('.history-select','检查文件预览问题');
  await until('document.querySelector(".sidebar-user-group")?.textContent.includes("检查文件预览问题")');
  await openRow('客户资料 项目操作');await clickText('.sidebar-context-menu button','置顶项目');
  await until('document.querySelector("[aria-label=置顶]")?.textContent.includes("客户资料")');
  await openRow('客户资料 项目操作');await clickText('.sidebar-context-menu button','取消置顶');
  await until('document.querySelector(".sidebar-user-group")?.textContent.includes("客户资料")');
  await openRow('客户资料 项目操作');await clickText('.sidebar-context-menu button','置顶项目');
  await openRow('整理项目文档 聊天操作');await clickText('.sidebar-context-menu button','置顶聊天');
  await until('document.querySelector("[aria-label=置顶]")?.textContent.includes("整理项目文档")');
  assert.equal(await read('document.querySelector(".conversation-list > section:first-child").getAttribute("aria-label")'),'置顶');

  await openRow('客户资料 项目操作');await clickText('.sidebar-context-menu button','归档项目');
  await until('!document.querySelector("[aria-label=置顶]")?.textContent.includes("客户资料")');
  await read('document.querySelector("[title=查看归档]").click()');
  await openRow('客户资料 项目操作');await snap('project-archive');
  await clickText('.sidebar-context-menu button','恢复项目');
  await read('document.querySelector("[title=返回侧栏列表]").click()');
  await until('document.querySelector("[aria-label=置顶]")?.textContent.includes("客户资料")');
}
async function finish(code){clearTimeout(deadline);try{await backend.stop();backend.detach();}catch(error){console.error(error);}window?.destroy();app.exit(code);}
app.once('browser-window-created',(_event,win)=>{
  window=win;win.webContents.on('console-message',details=>{if(details.level==='error')errors.push(details.message);});
  win.webContents.once('did-finish-load',()=>void run().then(()=>finish(0)).catch(async error=>{console.error(error.stack,errors);await snap('failure');await finish(1);}));
});
async function run(){
  await until('!!document.querySelector(".new-chat")');
  assert.deepEqual(Menu.getApplicationMenu().items.map(item=>item.label),['文件','编辑','视图','帮助']);
  assert.equal(Menu.getApplicationMenu().getMenuItemById('edit').submenu.items[0].role,'undo');
  assert.equal(window.isMenuBarVisible(),false);
  assert.equal(await read('document.querySelectorAll(".window-titlebar").length'),1);
  const docs=await rpc('conversation.create');await rpc('conversation.rename',{conversation_id:docs.id,title:'整理项目文档'});
  const review=await rpc('conversation.create');await rpc('conversation.rename',{conversation_id:review.id,title:'检查文件预览问题'});
  const report=await rpc('conversation.create');await rpc('conversation.rename',{conversation_id:report.id,title:'汇总本周工作进展'});
  const messages=[{id:'user',role:'user',content:'整理项目文档，让新同事能更快上手。'},{id:'answer',role:'assistant',content:'已整理好文档结构，按实际使用顺序分成快速开始、使用指南和开发说明。\n\n原有内容已保留，重复说明合并到对应章节。'}];
  snapshots.set(docs.id,{has_messages:true,messages,render_messages:messages});
  window.webContents.send('cowork:events',[{event:'conversation_changed',conversation_id:docs.id}]);
  await until('Array.from(document.querySelectorAll(".history-select")).some(button=>button.textContent==="检查文件预览问题")');
  await checkSidebar();
  command('search');await until('!!document.querySelector(".global-search input")');
  await until('document.activeElement===document.querySelector(".global-search input")');
  await fill('.global-search input','整理项目文档');await until('document.querySelectorAll(".global-search-result").length===1');
  await read('document.querySelector(".global-search input").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');
  await until('document.querySelector(".title-button")?.textContent==="整理项目文档"');
  await fill('[aria-label="消息输入"]','按这个结构继续完善快速开始部分。');
  command('sidebar');await until('!document.querySelector(".sidebar")');
  assert.equal(await read('!!document.querySelector(".navigation-tools [aria-label=搜索聊天与项目]")'),true);
  command('sidebar');await until('!!document.querySelector(".sidebar")');
  await until('getComputedStyle(document.querySelector(".app-navigation")).fontFamily.includes("Microsoft YaHei UI")');
  await read('document.fonts.ready.then(()=>true)');
  await snap('navigation');
  // The native popup is the same menu, and closing it resolves the toolbar state.
  await read('document.querySelector(".application-menus button").click()');
  await until('document.querySelector(".application-menus button").getAttribute("aria-expanded")==="true"');
  Menu.getApplicationMenu().getMenuItemById('file').submenu.closePopup(window);
  await until('document.querySelector(".application-menus button").getAttribute("aria-expanded")==="false"');
  command('search');await until('!!document.querySelector(".global-search input")');
  await fill('.global-search input','旧查询');await wait(()=>Boolean(slowSearch));
  await fill('.global-search input','新查询');await wait(()=>Boolean(fastSearch));
  const result={id:review.id,title:'新查询匹配的聊天',meta:{},updated_at:1};fastSearch([result]);
  await until('document.querySelector(".global-search-results").textContent.includes("新查询匹配")');slowSearch([{...result,title:'旧查询不应出现'}]);await delay(120);
  assert.equal(await read('document.querySelector(".global-search-results").textContent.includes("旧查询不应出现")'),false);
  searchFailure=true;await fill('.global-search input','失败');await until('!!document.querySelector(".navigation-search-error")');
  assert.equal(await read('document.querySelector(".global-search input").value'),'失败');
  searchFailure=false;await read('document.querySelector(".navigation-search-error button").click()');await until('!document.querySelector(".navigation-search-error") && !document.querySelector(".global-search-results[aria-busy=true]")');
  await fill('.global-search input','多结果');await until('document.querySelectorAll(".global-search-result").length===40');
  await read('document.querySelector(".global-search-results .sidebar-more").click()');await until('document.querySelectorAll(".global-search-result").length===65');
  await fill('.global-search input','文档');await until('document.querySelectorAll(".global-search-result").length>0');await snap('search');
  await read('document.querySelector(".global-search .section-heading button").click()');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'按这个结构继续完善快速开始部分。');
  await snap('desktop');
  await openRow('cross-platform-cowork 项目操作');await clickText('.sidebar-context-menu button','移到分组');await snap('sidebar-groups');
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));');
  await until('!document.querySelector(".sidebar-group-menu")');
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));');
  await until('!document.querySelector(".sidebar-context-menu")');
  await read('document.querySelector("[title=更多聊天工具]").click()');
  await clickText('.header-actions [role=menuitem]','查找当前聊天');
  await until('document.activeElement===document.querySelector("[aria-label=查找当前聊天]")');
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));');
  assert.equal(await read('!!document.querySelector(".history-popover")'),false);
  const stamp=Date.now()/1000;
  for(const [record,id,age] of [[review,'review-run',138],[report,'report-run',46]]){
    const run={id,conversation_id:record.id,status:'running',created_at:stamp-age,updated_at:stamp};runs.set(record.id,run);
    window.webContents.send('cowork:events',[{event:'run_started',conversation_id:record.id,run}]);
  }
  const question={type:'interaction_request',sequence:1,run_id:'review-run',data:{request_id:'question',run_id:'review-run'}};
  snapshots.set(review.id,{events:[question]});taskReadRelease=review.id;
  command('tasks');await until('document.querySelectorAll(".running-task").length===2');await wait(()=>typeof taskReadRelease==='function');
  window.webContents.send('cowork:events',[{event:'runtime',conversation_id:review.id,data:{type:'interaction_finished',sequence:2,run_id:'review-run',data:{request_id:'question'}}}]);
  taskReadRelease();taskReadRelease=null;await delay(120);
  assert.equal(await read('document.querySelector(".running-task").textContent.includes("等待你的回答")'),false);
  await snap('running-tasks');
  stopFailure=true;await read('document.querySelectorAll(".running-task-stop")[1].click()');await until('document.querySelector(".running-tasks [role=alert]")?.textContent.includes("停止未完成")');
  assert.equal(await read('document.querySelectorAll(".running-task").length'),2);stopFailure=false;
  await read('document.querySelectorAll(".running-task-stop")[1].click()');await until('document.querySelectorAll(".running-task").length===1');
  assert.deepEqual(stops,[{conversation_id:report.id,run_id:'report-run'}]);
  assert.equal(await read('document.querySelector(".title-button").textContent'),'整理项目文档');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'按这个结构继续完善快速开始部分。');
  await read('document.querySelectorAll(".running-task-actions button")[1].click()');await until('!!document.querySelector(".task-workspace")');
  assert.equal((await rpc('app.bootstrap')).last_conversation,review.id);
  // Empty state is driven by the durable run update, not a local success flag.
  runs.set(review.id,{...runs.get(review.id),status:'completed',updated_at:Date.now()/1000});
  window.webContents.send('cowork:events',[{event:'run_finished',conversation_id:review.id,run:runs.get(review.id)}]);
  command('tasks');await until('document.querySelector(".running-tasks")?.textContent.includes("当前没有正在运行")');
  await read('document.querySelector(".running-tasks header button").click()');
  // A real native webpage must park behind the global search, then resume.
  const localFile=await rpc('workspace.attach_data',{conversation_id:review.id,name:'navigation.html',base64:Buffer.from('<h1>Local navigation test</h1>').toString('base64')});
  const opened=await rpc('browser.open',{conversation_id:review.id,path:localFile.path});
  await until('Boolean([...document.querySelectorAll(".workspace-tab")].find(button=>button.textContent.includes("navigation")))');
  await read('[...document.querySelectorAll(".workspace-tab [role=tab]")].find(button=>button.textContent.includes("navigation")).click()');
  await wait(()=>browserHost?.visible?.id===opened.tab.id);
  await openRow('cross-platform-cowork 项目操作');await wait(()=>browserHost.visible===null);
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));');
  await wait(()=>browserHost.visible?.id===opened.tab.id);
  command('search');await until('!!document.querySelector(".global-search input")');await wait(()=>browserHost.visible===null);
  await read('document.querySelector(".global-search .section-heading button").click()');await wait(()=>browserHost.visible?.id===opened.tab.id);
  await read('document.querySelector("[title=收起工作区]").click()');
  window.setSize(1000,720);window.webContents.setZoomFactor(1.25);
  await rpc('settings.theme',{theme:'light'});await until('document.documentElement.dataset.theme==="light"');
  command('appearance');await until('document.documentElement.dataset.theme==="dark"');
  assert.equal(await read('(()=>{const brand=document.querySelector(".brand strong").getBoundingClientRect(),tools=document.querySelector(".brand .navigation-tools").getBoundingClientRect();return tools.left>=brand.right-1&&Math.abs((brand.top+brand.bottom)-(tools.top+tools.bottom))<=2;})()'),true);
  command('search');await until('!!document.querySelector(".global-search input")');await snap('search-dark-scaled');
  assert.equal(await read('document.documentElement.scrollWidth<=innerWidth+1'),true);
  assert.equal(await read('(()=>{const r=document.querySelector(".global-search").getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight})()'),true);
  assert.equal(await read('(()=>{const buttons=[...document.querySelectorAll(".app-navigation button")];return buttons.every(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})})()'),true);
  await read('document.querySelector(".global-search .section-heading button").click()');
  await read('document.querySelector(".sidebar-utilities button").click()');await until('!!document.querySelector(".management-nav")');
  assert.equal(await read('document.querySelector(".management-nav").textContent.includes("实验")'),false);
  await read('[...document.querySelectorAll(".sidebar-destinations button")].find(e=>e.textContent==="能力").click()');
  await until('[...document.querySelectorAll(".resource-title")].some(e=>e.textContent.includes("BrowserSkill"))');
  await read('[...document.querySelectorAll(".resource-title")].find(e=>e.textContent.includes("BrowserSkill")).click()');
  await until('document.body.textContent.includes("准备或修复组件")&&!document.querySelector(".settings-section input[type=checkbox]").disabled');
  assert.equal((await rpc('skills.browser_status')).enabled,false);
  await read('document.querySelector(".settings-section input[type=checkbox]").click()');
  await until('document.querySelector(".settings-section input[type=checkbox]").checked&&!document.querySelector(".settings-section input[type=checkbox]").disabled');
  assert.equal((await rpc('skills.browser_status')).enabled,true);
  const recovery=await rpc('skills.browser_action',{command:'recover'});assert(recovery.job_id);
  await snap('browser-skill-dark-scaled');
  await read('document.querySelector(".settings-section input[type=checkbox]").click()');
  await until('!document.querySelector(".settings-section input[type=checkbox]").checked&&!document.querySelector(".settings-section input[type=checkbox]").disabled');
  assert.deepEqual(errors,[]);
  console.log('NAVIGATION_OK '+JSON.stringify({native:Boolean(process.env.COWORK_NAVIGATION_NATIVE),scale:screen.getPrimaryDisplay().scaleFactor,zoom:window.webContents.getZoomFactor(),size:window.getSize()}));
}
require('../electron/main.cjs');
