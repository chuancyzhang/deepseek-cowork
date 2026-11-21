// Real main/preload/renderer and isolated service; only window visibility changes.
const electron=require('electron'),Module=require('node:module');
const {app}=electron,fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const originalLoad=Module._load;
const native=['1','hidden'].includes(process.env.COWORK_EDITOR_NATIVE);
app.getVersion=()=>require('../package.json').version;
class HiddenWindow extends electron.BrowserWindow {
  constructor(options){super({...options,show:false,webPreferences:{...options.webPreferences,offscreen:!native,backgroundThrottling:false}});}
  show(){if(process.env.COWORK_EDITOR_NATIVE==='1')super.show();} showInactive(){}
}
Module._load=function(name,...rest){return name==='electron'?{...electron,BrowserWindow:HiddenWindow}:originalLoad.call(this,name,...rest);};
if(!native)app.commandLine.appendSwitch('disable-gpu');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms)),errors=[];let window;
const deadline=setTimeout(()=>{console.error('EDITOR_TIMEOUT',errors);app.exit(1);},100000);
const read=async code=>{try{return await window.webContents.executeJavaScript(code);}catch(error){throw Error(code+'\n'+error.message);}};
const until=async(code,timeout=12000)=>{const start=Date.now();while(Date.now()-start<timeout){if(await read(`Boolean(${code})`))return;await delay(40);}throw Error('Timed out: '+code+'\n'+await read('document.body.innerText')+'\n'+errors.join('\n'));};
const click=async text=>{const selector=`[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===${JSON.stringify(text)}&&!el.closest('[hidden]'))`;await until(`Boolean(${selector})`);await read(`${selector}.click()`);};
const rpc=(method,params={})=>read(`window.cowork.call(${JSON.stringify(method)},${JSON.stringify(params)})`);
const snapshot=async(name,selector)=>{const target=process.env.COWORK_EDITOR_SCREENSHOTS;if(!target)return;window.webContents.invalidate();await delay(300);await fs.mkdir(target,{recursive:true});
  const rect=selector?await read(`(()=>{const {left,top,right,bottom}=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:Math.ceil(left),y:Math.ceil(top),width:Math.floor(right)-Math.ceil(left),height:Math.floor(bottom)-Math.ceil(top)};})()`):undefined;
  await fs.writeFile(path.join(target,name+'.png'),(await window.webContents.capturePage(rect)).toPNG());};
const input=async text=>read(`(()=>{const el=document.querySelector('[aria-label="消息输入"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,${JSON.stringify(text)});el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const key=async(keyCode,modifiers=[])=>{window.webContents.sendInputEvent({type:'keyDown',keyCode,modifiers});window.webContents.sendInputEvent({type:'keyUp',keyCode,modifiers});await delay(80);};
app.once('browser-window-created',(_event,win)=>{
  window=win;win.webContents.on('console-message',details=>{if(details.level==='error')errors.push(details.message);});
  win.webContents.once('did-finish-load',()=>void run().catch(async error=>{console.error(error.stack,errors);await snapshot('failure');try{await rpc('app.quit');}catch{app.exit(1);}process.exitCode=1;}));
});
async function run(){
  console.log('EDITOR_DISPLAY',JSON.stringify({native,visible:window.isVisible(),scaleFactor:electron.screen.getDisplayMatching(window.getBounds()).scaleFactor,bounds:window.getBounds()}));
  await until('Boolean(document.querySelector("[aria-label=消息输入]"))');
  await input('保留这条原草稿');await read('document.querySelector(".chat-header-tools [aria-label=工作区]").click()');
  await until('Boolean(document.querySelector(".workspace"))');
  const cid=(await rpc('app.bootstrap')).last_conversation,conversation=await rpc('conversation.get',{conversation_id:cid}),workspace=conversation.meta.workspace_dir;
  assert.ok(path.resolve(workspace).startsWith(path.resolve(process.env.COWORK_APP_DATA_DIR)),workspace);
  const contents={'Demo.java':'public class Demo {\r\n    public static void main(String[] args) {\r\n        System.out.println("你好");\r\n    }\r\n}\r\n',
    'demo.py':'def greet(name):\n    return f"你好，{name}"\n', 'demo.ts':'type User = {name: string};\nconst user: User = {name: "Cowork"};\n',
    'demo.js':'const greeting = "你好";\r\n'+Array.from({length:100},(_,i)=>'// 保留代码行 '+i+'\r\n').join(''),
    '说明.md':'# 说明\n\n保留写作预览。','notes.txt':'纯文本，不出现写作工具栏。'};
  for(const [name,text] of Object.entries(contents))await fs.writeFile(path.join(workspace,name),text);
  await read('document.querySelector("[aria-label=工作区文件]").click()');
  await until('document.querySelector(".file-navigator")');
  await read('document.querySelector("[title=固定文件导航]").click()');
  for(const [name,language] of [['Demo.java','java'],['demo.py','python'],['demo.ts','typescript'],['demo.js','javascript']]){
    await click(name);await until(`document.querySelector('.code-editor footer')?.textContent.includes(${JSON.stringify(language)})`);
    await until('document.querySelectorAll(".cm-content .cm-line span").length>0');
    assert.equal(await read('document.querySelectorAll(".workspace-content iframe").length'),0);
  }
  await read('document.querySelector(".cm-content").focus()');await key('End',['control']);await window.webContents.insertText('// 编辑草稿');await delay(100);
  await until('document.querySelector(".code-editor footer")?.textContent.includes("未保存")');
  await read('document.querySelector(".cm-scroller").scrollTop=250');await delay(100);
  await click('demo.py');await until('document.querySelector(".code-editor footer")?.textContent.includes("python")');
  await click('demo.js');await until('document.querySelector(".code-editor footer")?.textContent.includes("javascript")');
  assert.ok(await read('document.querySelector(".cm-scroller").scrollTop>0'));
  await read('document.querySelector(".cm-content").focus()');await key('Z',['control']);
  await until('!document.querySelector(".code-editor footer").textContent.includes("未保存")');
  await key('End',['control']);await window.webContents.insertText('// 保存验证');await key('S',['control']);
  await until('!document.querySelector(".code-editor footer").textContent.includes("未保存")');
  assert.equal(await fs.readFile(path.join(workspace,'demo.js'),'utf8'),contents['demo.js']+'// 保存验证');
  await click('Demo.java');await until('document.querySelector(".code-editor footer")?.textContent.includes("java")');
  await read('document.querySelector("[title=取消固定文件导航]").click();document.querySelector("[aria-label=工作区文件]").click()');
  await snapshot('code-editor-light');
  if (!await read('Boolean(document.querySelector(".cm-search"))')) await click('查找替换');
  await until('document.querySelector(".cm-search")');
  await rpc('themes.appearance',{value:{mode:'dark',reading:{}}});
  window.setMinimumSize(0,0);window.setSize(900,720);window.webContents.setZoomFactor(1.25);
  await until('document.documentElement.dataset.theme==="dark"');await delay(200);
  assert.ok(await read('document.querySelector(".code-editor").getBoundingClientRect().right<=innerWidth+1'));
  assert.ok(await read('document.documentElement.scrollWidth<=innerWidth+1'));
  await snapshot('code-editor-dark-scaled');
  window.setSize(1400,930);window.webContents.setZoomFactor(1);await rpc('themes.appearance',{value:{mode:'light',reading:{}}});
  await read('document.querySelector("[aria-label=工作区文件]").click()');await until('document.querySelector(".file-navigator")');
  await click('说明.md');await until('document.querySelector(".workspace-content iframe")');
  assert.equal(await read('document.querySelectorAll(".code-editor").length'),0);
  await read('document.querySelector("[aria-label=工作区文件]").click()');await until('document.querySelector(".file-navigator")');
  await click('notes.txt');await until('document.querySelector(".code-editor footer")?.textContent.includes("纯文本")');
  await read('document.querySelector("[title=收起工作区]").click()');
  await assert.rejects(rpc('settings.page',{page:'experiments'}),/设置页面不存在/);
  const setField=async(label,value)=>read(`(()=>{const el=document.querySelector('[aria-label="${label}"]');if(el instanceof HTMLInputElement)Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(value)});else el.value=${JSON.stringify(value)};el.dispatchEvent(new Event(el instanceof HTMLInputElement?'input':'change',{bubbles:true}));})()`);
  const openMedia=async()=>{await click('能力');await click('多媒体');await until('document.querySelector(".media-config-form")');};
  await openMedia();
  assert.equal(await read('document.querySelectorAll(".media-model-select").length'),3);
  assert.equal(await read('document.querySelector(".multimedia-plugin").textContent.includes("音频")'),false);
  assert.equal(await read('document.querySelector(".media-config-actions .primary").disabled'),true);
  await setField('视频生成模型','__add');await setField('模型服务','__new');
  await setField('模型标识','agnes-video-2.5-flash');await setField('服务名称','验证服务');
  await setField('服务地址','https://apihub.agnes-ai.com/v1');await setField('API Key','test-only-no-request');
  await read('document.querySelector(".media-inline-model summary").click()');await setField('接口协议','openai');
  await click('使用此模型');await until('document.querySelector(".inline-error")?.textContent.includes("Agnes")');
  assert.equal(await read('document.querySelector("[aria-label=模型标识]").value'),'agnes-video-2.5-flash');
  assert.equal(await read('JSON.stringify(localStorage).includes("test-only-no-request")'),false);
  assert.equal(Object.keys((await rpc('media.config')).bindings).length,0);
  await read('document.querySelector(".media-inline-model .inline-error").scrollIntoView({block:"center"})');await snapshot('multimedia-repair');
  await setField('接口协议','agnes');await click('使用此模型');await until('!document.querySelector(".media-inline-model")');
  await click('保存并启用');await until('document.querySelector(".media-config-actions .primary")?.textContent.includes("去聊天使用")');
  let media=await rpc('media.config');assert.equal(media.enabled,true);assert.equal(media.bindings.video_generate.model,'agnes-video-2.5-flash');
  const channelId=media.bindings.video_generate.channel_id;
  assert.equal((await rpc('media.tasks',{conversation_id:cid})).length,0);
  await until('!document.querySelector(".media-config-actions .primary").disabled');
  await snapshot('multimedia-config','.multimedia-plugin');
  await setField('视频生成模型','');await click('取消修改');
  await until('document.querySelector("[aria-label=视频生成模型]").value.includes("agnes-video")');
  await setField('视频生成模型','');await rpc('skills.enable',{name:'multimedia',enabled:false});await click('保存并停用');
  await until('document.querySelector(".inline-error")?.textContent.includes("已更新")');
  assert.equal(await read('document.querySelector("[aria-label=视频生成模型]").value'),'');
  await click('读取最新配置并保留编辑');await until('!document.querySelector(".inline-error")');
  assert.equal(await read('document.querySelector("[aria-label=视频生成模型]").value'),'');
  await click('取消修改');await click('保存并启用');await until('document.querySelector(".media-config-actions .primary")?.textContent.includes("去聊天使用")');
  await rpc('themes.appearance',{value:{mode:'dark',reading:{}}});window.setSize(900,720);window.webContents.setZoomFactor(1.25);
  await until('document.documentElement.dataset.theme==="dark"');await delay(250);
  assert.ok(await read('document.documentElement.scrollWidth<=innerWidth+1'));
  assert.ok(await read('document.querySelector(".media-config-actions .primary").getBoundingClientRect().right<=innerWidth+1'));
  await read('document.querySelector(".media-config-actions").scrollIntoView({block:"end"})');await snapshot('multimedia-dark-small');
  window.setSize(1400,930);window.webContents.setZoomFactor(1);await rpc('themes.appearance',{value:{mode:'light',reading:{}}});
  await click('去聊天使用');await until('document.querySelector("[aria-label=消息输入]")');
  assert.equal(await read('document.querySelector("[aria-label=消息输入]").value'),'保留这条原草稿');
  for(const model of ['agnes-video-2.5-flash','agnes-image-2.5-flash','my-video-chat-model'])await rpc('settings.save_model',{channel_id:channelId,model_name:model,name:'验证服务',base_url:'https://apihub.agnes-ai.com/v1'});
  const settings=await rpc('settings.get'),chat=settings.models.find(item=>item.model_name==='my-video-chat-model');
  await rpc('settings.select_model',{model_id:chat.id});
  await click('设置');await click('返回聊天');await until('document.querySelector("[aria-label=消息输入]")');
  await read('document.querySelector(".model-trigger").click()');await until('document.querySelector(".model-menu")');
  assert.ok(await read('document.querySelector(".model-menu .picker-list").textContent.includes("my-video-chat-model")'));
  assert.equal(await read('document.querySelector(".model-menu .picker-list").textContent.includes("agnes-video-2.5-flash")'),false);
  assert.equal(await read('document.querySelectorAll(".model-media-link").length'),0);
  await snapshot('chat-model-picker');await click('管理对话模型');
  await until('document.querySelector(".management-nav [aria-current=page]")?.textContent.includes("模型与服务")');
  await click('返回聊天');await until('document.querySelector("[aria-label=消息输入]")');

  await openMedia();
  const imageChoice=JSON.stringify([channelId,'agnes-image-2.5-flash','agnes']);
  const videoChoices=await read('[...document.querySelector("[aria-label=视频生成模型]").options].map(option=>option.textContent)');
  assert.equal(videoChoices.some(label=>label.includes('agnes-image-2.5-flash')),false);
  await setField('图片生成模型',imageChoice);await click('保存配置');
  await until('document.querySelector(".media-config-actions .primary")?.textContent.includes("去聊天使用")');
  await click('去聊天使用');await until('document.querySelector("[aria-label=消息输入]")');

  // A local provider exercises missing configuration -> skill page -> original task.
  const http=require('node:http');let analyses=0,requests=0;
  const server=http.createServer((req,res)=>{if(req.method==='GET'&&req.url==='/v1/models'){
    res.setHeader('Content-Type','application/json');
    return res.end(JSON.stringify({data:['fixture','model-one','model-two','model-three'].map(id=>({id}))}));
  }let raw='';req.on('data',chunk=>raw+=chunk);req.on('end',()=>{
    const body=JSON.parse(raw);requests++;console.log('MEDIA_FIXTURE_REQUEST',body.model,requests);
    if(body.model==='video-analysis'){analyses++;res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({choices:[{message:{content:'视频内容：本地验证完成。'}}]}));return;}
    const tools=body.messages.filter(message=>message.role==='tool');let name,args;
    if(!tools.length){name='tool_search';args={query:'multimedia video understand media_create'};}
    else if(!tools.some(message=>String(message.content).includes('"task_id"'))){name='media_create';args={capability:'video_understand',prompt:'概括视频内容',inputs:['https://example.test/video.mp4']};}
    const delta=name?{tool_calls:[{index:0,id:'call-'+requests,type:'function',function:{name,arguments:JSON.stringify(args)}}]}:{content:'需求已保留，请配置视频理解模型后继续。'};
    res.writeHead(200,{'Content-Type':'text/event-stream','Connection':'close'});
    res.end('data: '+JSON.stringify({id:'fixture',object:'chat.completion.chunk',created:1,model:'fixture',choices:[{index:0,delta,finish_reason:null}]})+'\n\ndata: '+JSON.stringify({id:'fixture',choices:[{index:0,delta:{},finish_reason:name?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
  });});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const localBase='http://127.0.0.1:'+server.address().port+'/v1';
    const updated=await rpc('settings.save_model',{name:'本地验证',model_name:'fixture',api_key:'local-fixture-key',base_url:localBase});
    await rpc('settings.permission',{enabled:true});await rpc('conversation.preferences',{conversation_id:cid,value:{selected_model_id:updated.selected_model_id}});
    await click('设置');await click('返回聊天');await until('document.querySelector("[aria-label=消息输入]")');
    await input('分析这个视频 https://example.test/video.mp4');await read('document.querySelector(".send-button").click()');
    await until('document.querySelector(".chat-media-tasks")?.textContent.includes("配置模型")',30000);
    const pending=(await rpc('media.tasks',{conversation_id:cid}))[0];assert.equal(pending.status,'needs_configuration');assert.equal(analyses,0);
    await click('配置模型');await until('document.querySelector(".media-config-form")');
    await setField('视频理解模型','__add');
    media=await rpc('media.config');const localChannel=media.channels.find(channel=>channel.base_url===localBase);
    await setField('模型服务',localChannel.channel_id);await setField('模型标识','video-analysis');
    await click('使用此模型');await until('!document.querySelector(".media-inline-model")');await click('保存配置');
    await until('document.querySelector(".media-config-actions .primary")?.textContent.includes("去聊天使用")');await click('去聊天使用');
    await click('继续任务');await until('document.querySelector(".chat-media-tasks")?.textContent.includes("已完成")');
    const complete=await rpc('media.tasks',{conversation_id:cid});assert.equal(complete.length,1);assert.equal(complete[0].id,pending.id);assert.equal(analyses,1);
    const original=await rpc('conversation.get',{conversation_id:cid});assert.equal(original.messages.filter(message=>message.role==='user').length,1);
    await snapshot('multimedia-chat');
    await openMedia();
    assert.equal(await read('document.querySelector("[aria-label=视频理解模型]").value.includes("video-analysis")'),true);
    await setField('视频理解模型','');
    await setField('视频生成模型','');
    await setField('图片生成模型','');
    await click('保存并停用');
    await until('document.querySelector(".media-config-actions .primary")?.textContent.includes("保存并启用")');
    assert.equal((await rpc('settings.get')).multimedia_enabled,false);
    assert.deepEqual((await rpc('media.config')).bindings,{});
    await click('返回聊天');await until('document.querySelector(".chat-media-tasks")?.textContent.includes("已完成")');
    assert.equal((await rpc('media.tasks',{conversation_id:cid})).length,1);

    const spare=await rpc('settings.save_model',{name:'备用服务',model_name:'spare',api_key:'spare-fixture-key',base_url:localBase});
    await rpc('settings.select_model',{model_id:updated.selected_model_id});
    window.setSize(960,760);window.webContents.setZoomFactor(1.25);
    await click('设置');await click('模型与服务');
    await until('[...document.querySelectorAll(".model-service h3")].some(el=>el.textContent==="本地验证")');
    await read('document.querySelector(\'[aria-label="编辑服务 本地验证"]\').click()');await click('获取模型');
    await until('document.querySelectorAll(".model-catalog input[type=checkbox]").length===3');
    const picked=()=>read('document.querySelectorAll(".model-catalog input:checked").length');
    assert.equal(await picked(),0);
    await click('全选');assert.equal(await picked(),3);await click('取消全选');assert.equal(await picked(),0);
    await read('[...document.querySelectorAll(".model-catalog .model-choice")].filter(el=>el.textContent!=="model-three").forEach(el=>el.click())');
    assert.equal(await picked(),2);await snapshot('model-catalog');
    assert.ok(await read('document.querySelector(".model-catalog").getBoundingClientRect().right<=innerWidth+1'));
    await click('添加 2 个模型');
    assert.equal((await rpc('settings.channels')).find(channel=>channel.display_name==='本地验证').models.length,1);
    await click('保存服务');
    await until('[...document.querySelectorAll(".model-service h3")].some(el=>el.textContent==="本地验证")');
    assert.equal((await rpc('settings.channels')).find(channel=>channel.display_name==='本地验证').models.length,3);
    const serviceMenu='[aria-label="本地验证 服务操作"]';
    const openDelete=async()=>{await read(`document.querySelector(${JSON.stringify(serviceMenu)}).click()`);await click('删除服务');await until('document.querySelector(".model-service-delete[open]")');};
    await openDelete();assert.equal(await read('document.querySelector(".delete-service-confirm").disabled'),true);
    await click('取消');await until('!document.querySelector(".model-service-delete")');
    assert.equal((await rpc('settings.get')).selected_model_id,updated.selected_model_id);
    await openDelete();
    assert.ok(await read('document.querySelector(".model-service-delete").getBoundingClientRect().right<=innerWidth+1'));
    await read(`(()=>{const field=document.querySelector(".model-service-delete select");field.value=${JSON.stringify(spare.selected_model_id)};field.dispatchEvent(new Event("change",{bubbles:true}));})()`);
    await snapshot('delete-service');await click('删除');await until('!document.querySelector(".model-service-delete")');
    assert.equal((await rpc('settings.get')).selected_model_id,spare.selected_model_id);
    assert.equal((await rpc('settings.channels')).some(channel=>channel.display_name==='本地验证'),false);
  } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  assert.equal(errors.filter(error=>/workbench_render_failed|Uncaught|Maximum update/.test(error)).length,0,errors.join('\n'));
  console.log('CODE_EDITOR_MEDIA_OK');
  await read('window.cowork.call("app.quit").catch(error => console.error(error.message)); undefined');
}
require('../electron/main.cjs');
