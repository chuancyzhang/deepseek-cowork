const electron=require('electron'),Module=require('node:module');
const {app,Menu,screen}=electron,fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const originalLoad=Module._load;
let selectedFolder;
const dialog=Object.create(electron.dialog);
dialog.showOpenDialog=(...args)=>selectedFolder
  ? Promise.resolve({canceled:false,filePaths:[selectedFolder]}) : electron.dialog.showOpenDialog(...args);
const openedFolders=[];
const shell=Object.create(electron.shell);
shell.openPath=async target=>{openedFolders.push(target);return '';};
app.getVersion=()=>require('../package.json').version;
class HiddenWindow extends electron.BrowserWindow {
  constructor(options){super({...options,show:false,webPreferences:{...options.webPreferences,offscreen:!process.env.COWORK_NAVIGATION_NATIVE,backgroundThrottling:false}});}
  show(){if(process.env.COWORK_NAVIGATION_NATIVE)super.showInactive();} showInactive(){this.show();}
}
Module._load=function(name,...rest){return name==='electron'?{...electron,dialog,shell,BrowserWindow:HiddenWindow}:originalLoad.call(this,name,...rest);};
app.commandLine.appendSwitch('disable-gpu');
const {Backend}=require('../electron/backend.cjs'),originalCall=Backend.prototype.call;
let window,backend,slowSearch,fastSearch,searchFailure=false,taskReadRelease,stopFailure=false,browserHost;
let preparingSearch=false, retryRequested=false;
const preparedTerms=[];
const errors=[],runs=new Map(),stops=[],snapshots=new Map();
const {BrowserHost}=require('../electron/browser.cjs'),originalShow=BrowserHost.prototype.show;
BrowserHost.prototype.show=function(params){browserHost=this;return originalShow.call(this,params);};
Backend.prototype.call=async function(method,p={}){
  backend=this;
  if(method==='conversation.search'){
    if(p.defer_index&&preparingSearch){preparedTerms.push(p.query);return {status:'preparing'};}
    if(p.query==='旧查询')return new Promise(resolve=>slowSearch=resolve);
    if(p.query==='新查询')return new Promise(resolve=>fastSearch=resolve);
    if(p.query==='失败'&&searchFailure)throw Error('测试搜索连接失败');
    if(p.query==='失败')retryRequested=p.retry_index;
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
async function snap(name, selector, padding=24) {
  const output=process.env.COWORK_NAVIGATION_SCREENSHOTS;
  if(!output)return;
  const rect=selector ? await read(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:Math.floor(r.left)-${padding},y:Math.floor(r.top)-${padding},width:Math.ceil(r.width)+${2*padding},height:Math.ceil(r.height)+${2*padding}};})()`) : undefined;
  await fs.mkdir(output,{recursive:true});
  window.webContents.invalidate();
  await delay(120);
  const image=rect ? await window.webContents.capturePage(rect) : await window.webContents.capturePage();
  await fs.writeFile(path.join(output,name+'.png'),image.toPNG());
}
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
async function checkSidebarWidth() {
  const rail='.sidebar-resizer';
  const center=await read(`(()=>{
    const rect=document.querySelector(${JSON.stringify(rail)}).getBoundingClientRect();
    return {x:Math.floor(rect.left+rect.width/2),y:Math.floor(rect.top+rect.height/2)};
  })()`);
  const client=window.webContents.debugger;
  client.attach('1.3');
  try {
    await client.sendCommand('Input.dispatchMouseEvent',{type:'mouseMoved',...center,button:'none',buttons:0});
    await client.sendCommand('Input.dispatchMouseEvent',{type:'mousePressed',...center,button:'left',buttons:1,clickCount:1});
    const moved={x:center.x+38,y:center.y};
    await client.sendCommand('Input.dispatchMouseEvent',{type:'mouseMoved',...moved,button:'none',buttons:1});
    await until('document.querySelector(".sidebar")?.offsetWidth===278');
    await client.sendCommand('Input.dispatchMouseEvent',{type:'mouseReleased',...moved,button:'left',buttons:0,clickCount:1});
  } finally {client.detach();}
  await until('localStorage.getItem("sidebar:width")==="278"');
  await snap('sidebar-resized');
  command('sidebar');await until('!document.querySelector(".sidebar")');
  command('sidebar');await until('document.querySelector(".sidebar")?.offsetWidth===278');
  const key=name=>read(`document.querySelector("${rail}").dispatchEvent(new KeyboardEvent("keydown",{key:${JSON.stringify(name)},bubbles:true}));`);
  await key('ArrowLeft');await until('document.querySelector(".sidebar")?.offsetWidth===270');
  await key('Home');await until('document.querySelector(".sidebar")?.offsetWidth===240');
}
async function checkModelPicker(name, short=false) {
  const composerTop=await read('document.querySelector(".composer").getBoundingClientRect().top');
  await read('document.querySelector(".model-trigger").click()');
  await until('document.activeElement===document.querySelector(".model-menu input")');
  const layout=await read(`(()=>{
    const area=document.querySelector(".conversation").getBoundingClientRect();
    const menu=document.querySelector(".model-menu"), box=menu.getBoundingClientRect();
    const search=menu.querySelector("input"), field=search.getBoundingClientRect();
    const footer=menu.querySelector(".single-menu > button").getBoundingClientRect();
    const list=menu.querySelector(".picker-list");
    return {inside:box.top>=area.top&&box.bottom<=area.bottom&&box.left>=area.left&&box.right<=area.right,
      reachable:document.elementFromPoint(field.left+field.width/2,field.top+field.height/2)===search&&footer.bottom<=box.bottom,
      scrolls:list.scrollHeight>list.clientHeight, top:document.querySelector(".composer").getBoundingClientRect().top};
  })()`);
  assert.equal(layout.inside,true);
  assert.equal(layout.reachable,true);
  assert.equal(layout.top,composerTop);
  if(short)assert.equal(layout.scrolls,true);
  await snap(name);
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}))');
  await until('!document.querySelector(".model-menu") && document.activeElement===document.querySelector(".model-trigger")');
}
async function checkProjectActions(original, occupied) {
  const oldChat=await rpc('conversation.create',{workspace_dir:original});
  await rpc('conversation.rename',{conversation_id:oldChat.id,title:'项目历史记录'});
  window.webContents.send('cowork:events',[{event:'conversation_changed',conversation_id:oldChat.id}]);
  await fill('[aria-label="消息输入"]','保留当前草稿');
  await openRow('客户资料 项目操作');
  await clickText('.sidebar-context-menu button','在资源管理器中打开');
  await wait(()=>openedFolders.includes(original));
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留当前草稿');

  await openRow('客户资料 项目操作');
  await clickText('.sidebar-context-menu button','编辑项目');
  await until('document.activeElement===document.querySelector(".project-dialog input")');
  assert.equal(await read('document.querySelector(".project-dialog input").value'),'客户资料');
  await snap('project-edit','.project-dialog');
  await fill('.project-dialog input','客户档案');
  selectedFolder=occupied;
  await clickText('.project-dialog button','更换…');
  await until('document.querySelector(".project-directory-row strong").textContent.endsWith("cross-platform-cowork")');
  await clickText('.project-dialog button','保存');
  await until('document.querySelector(".project-dialog [role=alert]")?.textContent.includes("已关联其他项目")');
  assert.equal(await read('document.querySelector(".project-dialog input").value'),'客户档案');
  await clickText('.project-dialog button','取消');
  await until('!!document.querySelector("[aria-label=\\"放弃项目修改？\\"][open]")');
  await clickText('dialog[aria-label="放弃项目修改？"] button','继续编辑');
  assert.equal(await read('document.querySelector(".project-dialog input").value'),'客户档案');
  const replacement=path.join(process.env.COWORK_APP_DATA_DIR,'客户档案');
  await fs.mkdir(replacement);
  selectedFolder=replacement;
  await clickText('.project-dialog button','更换…');
  await until('document.querySelector(".project-directory-row strong").textContent.endsWith("客户档案")');
  await clickText('.project-dialog button','保存');
  await until('!document.querySelector(".project-dialog") && document.querySelector("[aria-label=置顶]")?.textContent.includes("客户档案")');
  selectedFolder=undefined;
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留当前草稿');

  const row='.project-group:has([title="客户档案 项目操作"])';
  const expand=JSON.stringify(row+' .project-heading > button:first-child');
  const newChat=JSON.stringify(row+' .project-new-chat');
  await read('document.querySelector('+expand+'+"[aria-expanded=false]")?.click()');
  await until('document.querySelector('+JSON.stringify(row)+').textContent.includes("项目历史记录")');
  await read('document.querySelector('+expand+').click()');
  await read('document.querySelector('+newChat+').focus()');
  assert.equal(await read('getComputedStyle(document.querySelector('+newChat+')).opacity'),'1');
  await read('(()=>{const button=document.querySelector('+newChat+');button.click();button.click();})()');
  await until('document.querySelector(".home-project-trigger")?.title==='+JSON.stringify(replacement)+' && document.activeElement===document.querySelector("[aria-label=消息输入]")');
  assert.equal(await read('document.querySelector('+expand+').getAttribute("aria-expanded")'),'false');
  assert.equal((await rpc('conversation.list')).filter(chat=>chat.meta.workspace_dir===replacement).length,1);
  assert.equal((await rpc('conversation.get',{conversation_id:oldChat.id})).meta.workspace_dir,original);
}
async function checkHomeProjectMenu() {
  await until('document.querySelector(".workbench-home .home-project-trigger")?.textContent==="客户档案"');
  assert.deepEqual(await read('[...document.querySelectorAll(".home-shortcut-actions button")].map(button=>button.textContent)'),
    ['金融分析','数据分析','浏览器自动化','创造模式']);
  assert.equal(await read('Boolean(document.querySelector(".workbench-home .recent-task, .workbench-home .welcome-eyebrow"))'),false);
  assert.equal(await read('(()=>{const home=document.querySelector(".workbench-home").getBoundingClientRect(),title=document.querySelector(".workbench-home .welcome").getBoundingClientRect(),footer=document.querySelector(".home-composer-footer").getBoundingClientRect();return title.top-home.top>40&&home.bottom-footer.bottom>title.top-home.top;})()'),true);
  await fill('[aria-label="消息输入"]','');
  await snap('home');
  await checkModelPicker('model-picker');
  await read('document.querySelector(".home-project-trigger").click()');
  await until('document.activeElement===document.querySelector(".home-project-search input")');
  assert.equal(await read('document.querySelector(".home-project-list [aria-pressed=true]").textContent'),'客户档案');
  await snap('home-projects');
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}))');
  await until('!document.querySelector(".home-project-menu")&&document.activeElement===document.querySelector(".home-project-trigger")');

  await fill('[aria-label="消息输入"]','切换项目后保留这条草稿');
  await read('(()=>{const data=new DataTransfer();data.items.add(new File(["示例资料"],"首页草稿.txt",{type:"text/plain"}));document.querySelector("[aria-label=消息输入]").dispatchEvent(new ClipboardEvent("paste",{bubbles:true,clipboardData:data}));})()');
  await until('document.querySelector(".composer-contexts")?.textContent.includes("首页草稿.txt")');
  await read('document.querySelector(".home-project-trigger").click()');
  await fill('.home-project-search input','cross-platform');
  await until('document.querySelectorAll(".home-project-list button").length===1');
  const enterProject = () => read('document.querySelector(".home-project-search input").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true}))');
  const target = (await rpc('project.list')).find(item=>item.name==='cross-platform-cowork').path;
  assert.equal(path.dirname(target),path.join(process.env.COWORK_APP_DATA_DIR,'project_workspaces'));
  const parked = target + '-unavailable';
  await fs.rename(target,parked);
  try {
    await enterProject();
    await until('document.querySelector(".home-project-menu [role=alert]")?.textContent.includes("项目目录不可用")');
    assert.equal(await read('document.querySelector(".home-project-trigger").textContent'),'客户档案');
    assert.equal(await read('document.querySelector(".home-project-search input").value'),'cross-platform');
    assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'切换项目后保留这条草稿');
  } finally {await fs.rename(parked,target);}
  await enterProject();
  await until('document.querySelector(".home-project-trigger")?.textContent==="cross-platform-cowork"&&!document.querySelector(".home-project-menu")');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'切换项目后保留这条草稿');
  assert.equal(await read('document.querySelector(".composer-contexts").textContent.includes("首页草稿.txt")'),true);
  const project=await rpc('conversation.get',{conversation_id:(await rpc('app.bootstrap')).last_conversation});
  assert.equal(path.basename(project.meta.workspace_dir),'cross-platform-cowork');

  await read('document.querySelector(".home-project-trigger").click()');
  await clickText('.home-project-menu button','不在项目中工作');
  await until('document.querySelector(".home-project-trigger")?.textContent==="独立聊天"');
  const standalone=await rpc('conversation.get',{conversation_id:(await rpc('app.bootstrap')).last_conversation});
  assert.equal(standalone.meta.workspace_source,'chat');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'切换项目后保留这条草稿');
  await read('document.querySelector(".home-project-trigger").click()');
  await clickText('.home-project-menu button','新建项目');
  await until('document.activeElement===document.querySelector(".project-dialog input")');
  await fill('.project-dialog input','首页示例项目');
  await clickText('.project-dialog button','保存');
  await until('!document.querySelector(".project-dialog") && document.querySelector(".home-project-trigger")?.textContent==="首页示例项目"');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'切换项目后保留这条草稿');
}

async function checkUsageLayout(placement) {
  const layout = await read(`(() => {
    const bounds = selector => document.querySelector(selector)?.getBoundingClientRect();
    const scroll = document.querySelector('.message-scroll'), viewport = scroll.getBoundingClientRect();
    const usage = bounds('.usage-popover'), chat = bounds('.conversation');
    const content = bounds('.messages'), composer = bounds('.composer'), workspace = bounds('.workspace');
    const field = document.querySelector('[aria-label=消息输入]');
    const input = field.getBoundingClientRect();
    const beside = content.right <= usage.left + 1;
    return {placement:beside ? 'right' : 'overlay',
      aligned: Math.abs(composer.left - content.left) < 1 && Math.abs(composer.right - content.right) < 1,
      scrollAtEdge: Math.abs(viewport.right - chat.right) < 1 && scroll.scrollWidth <= scroll.clientWidth + 1,
      inputReachable: composer.top >= usage.bottom && composer.bottom <= innerHeight + 1 &&
        field === document.elementFromPoint(input.left + input.width / 2, input.top + input.height / 2),
      staysInChat: usage.top >= viewport.top - 1 && usage.bottom <= viewport.bottom + 1 &&
        (!workspace || usage.right <= workspace.left + 1)};
  })()`);
  if (placement) assert.equal(layout.placement, placement);
  assert.equal(layout.aligned && layout.scrollAtEdge && layout.inputReachable && layout.staysInChat, true, JSON.stringify(layout));
}

const composerBounds = () => read('document.querySelector(".composer").getBoundingClientRect().toJSON()');

async function checkChatHeader() {
  assert.deepEqual(await read('[...document.querySelectorAll(".chat-header-tools button")].map(button=>[button.getAttribute("aria-label"),button.textContent.trim(),button.hasAttribute("title")])'),
    [['历史提问','',false],['用量统计','',false],['工作区','',false]]);
  assert.equal(await read('Boolean(document.querySelector("[title=更多聊天工具]"))'),false);
  await read('document.querySelector(".chat-header-tools [aria-label=历史提问]").click()');
  await until('document.querySelector(".history-popover")?.textContent.includes("让新同事能更快上手")');
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}))');
  await until('!document.querySelector(".history-popover")&&document.activeElement.getAttribute("aria-label")==="历史提问"');
  const beforeUsage = await composerBounds();
  await read('document.querySelector(".chat-header-tools [aria-label=用量统计]").click()');
  await until('!!document.querySelector(".usage-overview")');
  assert.ok(await read('(()=>{const panel=document.querySelector(".usage-overview").getBoundingClientRect();return panel.width<=266&&panel.height<=160;})()'));
  assert.equal(await read('document.getElementById(document.querySelector(".chat-header-tools [aria-label=用量统计]").getAttribute("aria-controls"))===document.querySelector(".usage-overview")'),true);
  assert.deepEqual(await read('[...document.querySelectorAll(".usage-overview-metrics dd")].map(el=>el.textContent)'),['40,610','99,310','61%']);
  assert.equal(await read('Boolean(document.querySelector(".usage-budget, .usage-files ul"))'),false);
  assert.equal(await read('document.querySelector(".usage-files summary").getAttribute("aria-label")'),'文件，2 个');
  await checkUsageLayout('right');
  const besideUsage = await composerBounds();
  assert.ok(besideUsage.left < beforeUsage.left);
  assert.equal(besideUsage.top, beforeUsage.top);
  await read('document.querySelector("[aria-label=消息输入]").dispatchEvent(new PointerEvent("pointerdown",{bubbles:true}));document.querySelector("[aria-label=消息输入]").focus()');
  assert.equal(await read('!!document.querySelector(".usage-overview")&&document.activeElement===document.querySelector("[aria-label=消息输入]")'),true);
  await snap('usage','.main-shell',0);
  await read('document.querySelector(".chat-header-tools [aria-label=历史提问]").click()');
  await until('!!document.querySelector(".history-popover")&&!!document.querySelector(".usage-overview")');
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}))');
  await until('!document.querySelector(".history-popover")&&!!document.querySelector(".usage-overview")&&document.activeElement.getAttribute("aria-label")==="历史提问"');
  await read('document.querySelector(".usage-files summary").click()');
  await until('document.querySelectorAll(".usage-files li").length===2');
  assert.deepEqual(await read('[...document.querySelectorAll(".usage-files .usage-file-name")].map(el=>el.textContent)'),['快速开始.md','需求说明.txt']);
  await checkUsageLayout('right');
  assert.deepEqual(await composerBounds(), besideUsage);
  await snap('usage-files','.usage-popover',0);
  await clickText('.usage-reveal','详情');
  await until('document.querySelector(".usage-budget")?.textContent.includes("300,000 Token")');
  await checkUsageLayout('right');
  assert.deepEqual(await composerBounds(), besideUsage);
  assert.equal(await read('document.activeElement.getAttribute("aria-label")'),'返回用量概览');
  assert.equal(await read('document.querySelector(".usage-popover select").value'),'1000000');
  assert.equal(await read('document.querySelector(".usage-limit label").textContent'),'下次目标');
  assert.equal(await read('document.querySelector(".usage-capacity").getAttribute("aria-valuetext")'),'40,830 / 300,000 Token');
  await snap('usage-details','.usage-popover',0);
  for(const value of ['300000','1000000']){
    await read(`(()=>{const select=document.querySelector('.usage-popover select');select.value=${JSON.stringify(value)};select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await clickText('.usage-popover .button-row button','保存');
    await until('!document.querySelector(".usage-popover .button-row")');
    assert.equal((await rpc('settings.get')).context_policy.target_tokens,Number(value));
    assert.equal(await read('document.querySelector(".usage-capacity").getAttribute("aria-valuemax")'),'300000');
  }
  await read('document.querySelector("[aria-label=返回用量概览]").click()');
  await until('document.activeElement===document.querySelector(".usage-reveal")&&document.querySelectorAll(".usage-files li").length===2');
  await clickText('.usage-files li > button','快速开始.md');
  await until('[...document.querySelectorAll(".workspace-tab")].some(tab=>tab.textContent.includes("快速开始.md"))&&document.querySelector(".usage-overview")?.checkVisibility()===false');
  assert.equal(await read('document.querySelector(".chat-header-tools [aria-label=用量统计]").getAttribute("aria-expanded")'),'false');
  await read('document.querySelector(".chat-header-tools [aria-label=工作区]").click()');
  await until('!document.querySelector(".workspace-bar")&&document.querySelector(".usage-overview")?.checkVisibility()&&!!document.querySelector(".usage-files[open]")');
  await checkUsageLayout('right');
  await clickText('.usage-reveal','详情');
  await until('!!document.querySelector("[aria-label=用量详情]")');
  await read('document.querySelector(".chat-header-tools [aria-label=工作区]").click()');
  await until('!!document.querySelector(".workspace-bar")&&document.querySelector("[aria-label=用量详情]")?.checkVisibility()===false');
  await read('document.querySelector(".chat-header-tools [aria-label=用量统计]").click()');
  await until('!document.querySelector(".workspace-bar")&&document.querySelector("[aria-label=用量详情]")?.checkVisibility()');

  await read('document.querySelector(".chat-header-tools [aria-label=用量统计]").click()');
  await until('!document.querySelector(".usage-popover")&&document.activeElement.getAttribute("aria-label")==="用量统计"');
  assert.deepEqual(await composerBounds(), beforeUsage);
  await read('document.querySelector(".chat-header-tools [aria-label=用量统计]").click()');
  await until('!!document.querySelector(".usage-overview")&&!document.querySelector(".usage-files[open]")');
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}))');
  assert.equal(await read('!!document.querySelector(".usage-overview")'),true);
  await read('document.querySelector("[aria-label=关闭用量]").click()');
  await until('!document.querySelector(".usage-popover")&&document.activeElement.getAttribute("aria-label")==="用量统计"');
  assert.deepEqual(await composerBounds(), beforeUsage);
  await read('document.querySelector(".chat-header-tools [aria-label=工作区]").click()');
  await until('!!document.querySelector(".workspace-bar")&&document.querySelector(".chat-header-tools [aria-label=工作区]").getAttribute("aria-expanded")==="true"');
  await read('document.querySelector(".chat-header-tools [aria-label=工作区]").click()');
  await until('!document.querySelector(".workspace-bar")&&!document.querySelector(".usage-popover")');
}

async function checkUsagePersistence(report) {
  const example = {id: 'usage-other-chat', role: 'user', content: '汇总本周的进展。'};
  snapshots.set(report.id, {has_messages: true, messages: [example], render_messages: [example], events: []});
  await clickText('.history-select > span','整理项目文档');
  await until('!!document.querySelector("#message-user")&&!!document.querySelector("[aria-label=用量统计]")');
  await read('document.querySelector("[aria-label=用量统计]").click()');
  await clickText('.usage-reveal','详情');
  await until('!!document.querySelector("[aria-label=用量详情]")');
  await clickText('.history-select > span','汇总本周工作进展');
  const reportVisible = '!!document.querySelector("#message-usage-other-chat")&&!!document.querySelector("[aria-label=消息输入]")';
  await until(reportVisible + '&&!!document.querySelector(".usage-overview")');
  assert.deepEqual(await read('[...document.querySelectorAll(".usage-overview-metrics dd")].map(el=>el.textContent)'), ['—','—','—']);
  assert.equal(await read('!!document.querySelector(".usage-files")'),false);

  await read('document.querySelector(".sidebar-utilities button").click()');
  await until('!!document.querySelector(".back-link")&&!document.querySelector(".usage-popover")');
  await read('document.querySelector(".back-link").click()');
  await until(reportVisible + '&&!!document.querySelector(".usage-overview")');

  const reload = () => new Promise(resolve => {
    window.webContents.once('did-finish-load',resolve);
    window.webContents.reload();
  });
  await reload();
  await until(reportVisible + '&&!!document.querySelector(".usage-overview")');
  assert.equal(await read('!!document.activeElement.closest(".usage-popover")'),false);
  await read('document.querySelector("[aria-label=关闭用量]").click()');
  await reload();
  await until(reportVisible + '&&document.querySelector("[aria-label=用量统计]")?.getAttribute("aria-expanded")==="false"');
  assert.equal(await read('!!document.querySelector(".usage-popover")'),false);
}

async function checkSidebar() {
  await until('document.querySelectorAll(".brand .navigation-tools button").length===2');
  command('sidebar');await until('document.querySelectorAll(".app-navigation .navigation-tools button").length===2');
  command('sidebar');await until('document.querySelectorAll(".brand .navigation-tools button").length===2');
  const projectActions='.sidebar [aria-label="项目"] .sidebar-section-actions';
  await read('document.activeElement?.blur()');
  const hover=await read('matchMedia("(hover: hover) and (pointer: fine)").matches');
  if(hover)assert.equal(await read(`getComputedStyle(document.querySelector(${JSON.stringify(projectActions)})).opacity`),'0');
  await read(`document.querySelector(${JSON.stringify(projectActions)}).querySelector('button').focus()`);
  assert.equal(await read(`getComputedStyle(document.querySelector(${JSON.stringify(projectActions)})).opacity`),'1');
  await read('document.activeElement?.blur()');
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
  await dragSidebar('.history-select > span','检查文件预览问题');
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
  await checkProjectActions(projects.find(project=>project.name==='客户资料').path,path.join(root,'cross-platform-cowork'));
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
  const examples=['通用对话','长文档分析','快速问答','代码助手','数据分析','任务规划','文本整理','推理模型'];
  for(const [index, display_name] of examples.entries()) {
    await rpc('settings.save_model',{channel_id:'demo',model_id:'demo-'+index,name:'示例服务',model_name:'chat-'+index,display_name,base_url:'http://127.0.0.1:1/v1'});
  }
  await rpc('settings.select_model',{model_id:'demo-0'});
  window.webContents.send('cowork:events',[{event:'settings_changed'}]);
  const input=await rpc('workspace.attach_data',{conversation_id:docs.id,name:'需求说明.txt',base64:Buffer.from('先介绍项目的启动流程。').toString('base64')});
  const output=await rpc('workspace.create',{conversation_id:docs.id,name:'快速开始.md',text:'# 快速开始\n\n从工作台打开项目。'});
  const messages=[
    {id:'user',role:'user',content:'整理项目文档，让新同事能更快上手。',meta:{desktop_attachments:[input]}},
    {id:'answer',role:'assistant',content:'已把项目的快速开始、使用指南和开发说明整理到快速开始.md。',
      file_references:[{path:output.path,url:pathToFileURL(output.path).href,name:'快速开始.md',kind:'markdown',label:'Markdown',references:['快速开始.md']}]},
  ];
  const counts={user:1432,assistant:12128,results:15940,system:6230,tools:3500,injected:1220};
  const usageEvents=[
    {type:'observability',data:{type:'system_prompt',context_usage:{status:'available',counts,total:40450},
      context_capacity:{status:'available',used_tokens:40830,effective_tokens:300000,target_tokens:300000}}},
    {type:'observability',data:{type:'provider_request_start',request_id:'example-provider'},created_at:100},
    {type:'content',delta:messages[1].content,source_message_id:'answer',created_at:101},
    {type:'observability',data:{type:'llm_usage',usage:{request_id:'example-provider',input_tokens:38520,output_tokens:2090,cached_input_tokens:26520}}},
    {type:'observability',data:{type:'provider_request_finish',request_id:'example-provider',status:'completed'},created_at:102.1},
  ].map((event,index)=>({...event,run_id:'example-usage',sequence:index+1}));
  const priorUsage={type:'observability',run_id:'earlier-usage',sequence:1,data:{type:'llm_usage',
    usage:{request_id:'earlier-provider',input_tokens:54500,output_tokens:4200,cached_input_tokens:30000}}};
  snapshots.set(docs.id,{has_messages:true,messages,render_messages:messages,events:[priorUsage,...usageEvents]});
  window.webContents.send('cowork:events',[{event:'conversation_changed',conversation_id:docs.id}]);
  await until('Array.from(document.querySelectorAll(".history-select > span")).some(label=>label.textContent==="检查文件预览问题")');
  assert.match(await read('document.querySelector(".history-select time").textContent'),/^\d{2}:\d{2}$/);
  await checkSidebarWidth();
  await checkSidebar();
  await checkHomeProjectMenu();
  command('search');await until('!!document.querySelector(".global-search input")');
  await until('document.activeElement===document.querySelector(".global-search input")');
  await fill('.global-search input','整理项目文档');await until('document.querySelectorAll(".global-search-result").length===1');
  await read('document.querySelector(".global-search input").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');
  await until('document.querySelector(".title-button")?.textContent==="整理项目文档"&&!!document.querySelector("[aria-label=消息输入]")');
  await fill('[aria-label="消息输入"]','按这个结构继续完善快速开始部分。');
  command('sidebar');await until('!document.querySelector(".sidebar")');
  assert.equal(await read('!!document.querySelector(".navigation-tools [aria-label=搜索聊天与项目]")'),true);
  command('sidebar');await until('!!document.querySelector(".sidebar")');
  await until('getComputedStyle(document.querySelector(".app-navigation")).fontFamily.includes("Microsoft YaHei UI")');
  await read('document.fonts.ready.then(()=>true)');
  assert.equal(await read(`(()=>{
    const title=document.querySelector(".window-titlebar").getBoundingClientRect(), content=document.querySelector(".window-content");
    const native=navigator.windowControlsOverlay?.getTitlebarAreaRect();
    return getComputedStyle(content).borderTopWidth!=="0px" && content.getBoundingClientRect().top===title.bottom &&
      title.bottom >= (native?.y||0)+(native?.height||0);
  })()`),true);
  await snap('navigation');
  // The native popup is the same menu, and closing it resolves the toolbar state.
  await read('document.querySelector(".application-menus button").click()');
  await until('document.querySelector(".application-menus button").getAttribute("aria-expanded")==="true"');
  Menu.getApplicationMenu().getMenuItemById('file').submenu.closePopup(window);
  await until('document.querySelector(".application-menus button").getAttribute("aria-expanded")==="false"');
  command('search');await until('!!document.querySelector(".global-search input")');
  preparingSearch=true;
  await fill('.global-search input','初始查询');
  await until('document.querySelector(".navigation-empty")?.textContent.includes("正在准备聊天搜索")');
  await fill('.global-search input','旧查询');
  await wait(()=>preparedTerms.includes('旧查询'));
  assert.deepEqual([...new Set(preparedTerms)],['初始查询','旧查询']);
  preparingSearch=false;
  await wait(()=>Boolean(slowSearch));
  await fill('.global-search input','新查询');await wait(()=>Boolean(fastSearch));
  const result={id:review.id,title:'新查询匹配的聊天',meta:{},updated_at:1};fastSearch([result]);
  await until('document.querySelector(".global-search-results").textContent.includes("新查询匹配")');slowSearch([{...result,title:'旧查询不应出现'}]);await delay(120);
  assert.equal(await read('document.querySelector(".global-search-results").textContent.includes("旧查询不应出现")'),false);
  searchFailure=true;await fill('.global-search input','失败');await until('!!document.querySelector(".navigation-search-error")');
  assert.equal(await read('document.querySelector(".global-search input").value'),'失败');
  searchFailure=false;await read('document.querySelector(".navigation-search-error button").click()');await until('!document.querySelector(".navigation-search-error") && !document.querySelector(".global-search-results[aria-busy=true]")');
  assert.equal(retryRequested,true);
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
  await checkChatHeader();
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
  await clickText('.sidebar-context-menu button','编辑项目');await until('!!document.querySelector(".project-dialog[open]")');
  assert.equal(browserHost.visible,null);
  await clickText('.project-dialog button','取消');
  await wait(()=>browserHost.visible?.id===opened.tab.id);
  command('search');await until('!!document.querySelector(".global-search input")');await wait(()=>browserHost.visible===null);
  await read('document.querySelector(".global-search .section-heading button").click()');await wait(()=>browserHost.visible?.id===opened.tab.id);
  await read('document.querySelector("[title=收起工作区]").click()');
  window.setSize(1000,720);window.webContents.setZoomFactor(1.25);
  await until('innerWidth<=801&&innerHeight<=577');
  await rpc('settings.theme',{theme:'light'});await until('document.documentElement.dataset.theme==="light"');
  command('appearance');await until('document.documentElement.dataset.theme==="dark"');
  assert.equal(await read('(()=>{const brand=document.querySelector(".brand strong").getBoundingClientRect(),tools=document.querySelector(".brand .navigation-tools").getBoundingClientRect();return tools.left>=brand.right-1&&Math.abs((brand.top+brand.bottom)-(tools.top+tools.bottom))<=2;})()'),true);
  command('search');await until('!!document.querySelector(".global-search input")');await snap('search-dark-scaled');
  assert.equal(await read('document.documentElement.scrollWidth<=innerWidth+1'),true);
  assert.equal(await read('(()=>{const r=document.querySelector(".global-search").getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight})()'),true);
  assert.equal(await read('(()=>{const buttons=[...document.querySelectorAll(".app-navigation button")];return buttons.every(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})})()'),true);
  await read('document.querySelector(".global-search .section-heading button").click()');
  await openRow('cross-platform-cowork 项目操作');
  await clickText('.sidebar-context-menu button','编辑项目');
  await until('!!document.querySelector(".project-dialog[open]")');
  assert.equal(await read('(()=>{const form=document.querySelector(".project-dialog").getBoundingClientRect();const path=document.querySelector(".project-directory-row");return form.left>=0&&form.right<=innerWidth&&form.top>=0&&form.bottom<=innerHeight&&path.scrollWidth<=path.clientWidth+1})()'),true);
  await snap('project-edit-dark-scaled');
  await clickText('.project-dialog button','取消');
  await until('!document.querySelector(".project-dialog")');
  await clickText('.history-select > span','整理项目文档');
  await until('document.querySelectorAll(".chat-header-tools button").length===3&&!!document.querySelector("#message-user")&&!!document.querySelector(".composer")');
  assert.equal(await read('(()=>{const buttons=[...document.querySelectorAll(".chat-header-tools button")].map(button=>button.getBoundingClientRect());return buttons.every(rect=>rect.top===buttons[0].top&&rect.left>=0&&rect.right<=innerWidth);})()'),true);
  const beforeUsage = await composerBounds();
  await read('document.querySelector(".chat-header-tools [aria-label=用量统计]").click()');
  await until('!!document.querySelector(".usage-overview")');
  assert.equal(await read('document.querySelector(".usage-overview").scrollWidth<=document.querySelector(".usage-overview").clientWidth+1'),true);
  await checkUsageLayout('overlay');
  assert.deepEqual(await composerBounds(), beforeUsage);
  await read('document.querySelector(".usage-files summary").click()');
  await until('document.querySelectorAll(".usage-files li").length===2');
  await checkUsageLayout('overlay');
  assert.deepEqual(await composerBounds(), beforeUsage);
  await snap('usage-overview-dark-scaled');
  await read('document.querySelector(".usage-reveal").scrollIntoView({block:"nearest"})');
  await clickText('.usage-reveal','详情');
  await until('document.querySelector(".usage-capacity")?.getAttribute("aria-valuemax")==="300000"');
  await checkUsageLayout('overlay');
  assert.deepEqual(await composerBounds(), beforeUsage);
  assert.equal(await read('(()=>{const r=document.querySelector(".usage-popover").getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;})()'),true);
  await snap('usage-dark-scaled');
  const usageSnapshot = snapshots.get(docs.id);
  const history = Array.from({length: 8}, (_, index) => messages.map(message => ({
    id: 'usage-history-' + index + '-' + message.id, role: message.role, content: message.content
  }))).flat().concat(messages);
  snapshots.set(docs.id,{...usageSnapshot,messages:history,render_messages:history});
  window.webContents.send('cowork:events',[{event:'conversation_changed',conversation_id:docs.id}]);
  await until('!!document.querySelector("#message-usage-history-0-user")');
  const usageRun = {id:'usage-layout-run',conversation_id:docs.id,status:'running',created_at:stamp,updated_at:stamp};
  runs.set(docs.id,usageRun);
  window.webContents.send('cowork:events',[
    {event:'run_started',conversation_id:docs.id,run:usageRun},
    {event:'runtime',conversation_id:docs.id,data:{type:'content',delta:'正在整理项目文档。',source_message_id:'usage-live-reply',run_id:usageRun.id,sequence:1}},
  ]);
  await until('!!document.querySelector(".message-scroll .process-summary .sr-only")');
  assert.equal(await read('document.scrollingElement.scrollHeight<=innerHeight+1'),true);
  assert.deepEqual(await composerBounds(), beforeUsage);
  await read('document.querySelector(".usage-popover [aria-label=关闭用量]").click()');
  snapshots.set(docs.id,usageSnapshot);
  runs.delete(docs.id);
  window.webContents.send('cowork:events',[{event:'run_finished',conversation_id:docs.id,run:{...usageRun,status:'completed',updated_at:Date.now()/1000}}]);
  command('new-chat');await until('!!document.querySelector(".workbench-home .home-project-trigger")&&document.activeElement===document.querySelector("[aria-label=消息输入]")');
  window.setSize(850,620);
  window.webContents.setZoomFactor(1.5);
  await until('innerHeight<440');
  await checkModelPicker('model-picker-dark-short',true);
  window.setSize(1000,720);
  window.webContents.setZoomFactor(1.25);
  await read('document.querySelector(".home-project-trigger").click()');
  await until('!!document.querySelector(".home-project-menu")');
  assert.equal(await read('(()=>{const r=document.querySelector(".home-project-menu").getBoundingClientRect();return r.top>=0&&r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&document.documentElement.scrollWidth<=innerWidth+1;})()'),true);
  await snap('home-projects-dark-scaled');
  await read('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}))');
  await until('!document.querySelector(".home-project-menu")');
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
  await checkUsagePersistence(report);
  assert.deepEqual(errors,[]);
  console.log('NAVIGATION_OK '+JSON.stringify({native:Boolean(process.env.COWORK_NAVIGATION_NATIVE),scale:screen.getPrimaryDisplay().scaleFactor,zoom:window.webContents.getZoomFactor(),size:window.getSize()}));
}
require('../electron/main.cjs');
