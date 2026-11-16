// Real main/preload/renderer and isolated service; only window visibility changes.
const electron=require('electron'),Module=require('node:module');
const {app}=electron,fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const originalLoad=Module._load;
app.getVersion=()=>require('../package.json').version;
class HiddenWindow extends electron.BrowserWindow {
  constructor(options){super({...options,show:false,webPreferences:{...options.webPreferences,offscreen:process.env.COWORK_EDITOR_NATIVE!=='1',backgroundThrottling:false}});}
  show(){if(process.env.COWORK_EDITOR_NATIVE==='1')super.show();} showInactive(){}
}
Module._load=function(name,...rest){return name==='electron'?{...electron,BrowserWindow:HiddenWindow}:originalLoad.call(this,name,...rest);};
if(process.env.COWORK_EDITOR_NATIVE!=='1')app.commandLine.appendSwitch('disable-gpu');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms)),errors=[];let window;
const deadline=setTimeout(()=>{console.error('EDITOR_TIMEOUT',errors);app.exit(1);},100000);
const read=async code=>{try{return await window.webContents.executeJavaScript(code);}catch(error){throw Error(code+'\n'+error.message);}};
const until=async code=>{const start=Date.now();while(Date.now()-start<12000){if(await read(`Boolean(${code})`))return;await delay(40);}throw Error('Timed out: '+code+'\n'+await read('document.body.innerText')+'\n'+errors.join('\n'));};
const click=async text=>{const selector=`[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===${JSON.stringify(text)}&&!el.closest('[hidden]'))`;await until(`Boolean(${selector})`);await read(`${selector}.click()`);};
const rpc=(method,params={})=>read(`window.cowork.call(${JSON.stringify(method)},${JSON.stringify(params)})`);
const snapshot=async name=>{const target=process.env.COWORK_EDITOR_SCREENSHOTS;if(!target)return;window.webContents.invalidate();await delay(300);await fs.mkdir(target,{recursive:true});await fs.writeFile(path.join(target,name+'.png'),(await window.webContents.capturePage()).toPNG());};
const input=async text=>read(`(()=>{const el=document.querySelector('[aria-label="消息输入"]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,${JSON.stringify(text)});el.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const key=async(keyCode,modifiers=[])=>{window.webContents.sendInputEvent({type:'keyDown',keyCode,modifiers});window.webContents.sendInputEvent({type:'keyUp',keyCode,modifiers});await delay(80);};
app.once('browser-window-created',(_event,win)=>{
  window=win;win.webContents.on('console-message',details=>{if(details.level==='error')errors.push(details.message);});
  win.webContents.once('did-finish-load',()=>void run().catch(async error=>{console.error(error.stack,errors);await snapshot('failure');try{await rpc('app.quit');}catch{app.exit(1);}process.exitCode=1;}));
});
async function run(){
  console.log('EDITOR_DISPLAY',JSON.stringify({native:process.env.COWORK_EDITOR_NATIVE==='1',scaleFactor:electron.screen.getDisplayMatching(window.getBounds()).scaleFactor,bounds:window.getBounds()}));
  await until('Boolean(document.querySelector("[aria-label=消息输入]"))');
  await input('保留这条原草稿');await click('工作区');
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
  await click('查找替换');await until('document.querySelector(".cm-search")');
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
  let page=await rpc('settings.page',{page:'experiments'});await rpc('settings.page_save',{page:'experiments',revision:page.revision,value:{...page.values,multimedia:true}});
  await read('document.querySelector("[aria-label=添加上下文]").click()');await click('生成视频需配置');
  await until('document.querySelector(".media-binding-editor")');
  assert.ok(await read('document.querySelector(".media-binding-editor").textContent.includes("视频生成")'));
  let media=await rpc('media.config');await rpc('media.save',{revision:media.revision,bindings:{video_generate:{channel_id:'fixture',model:'agnes-video-2.5-flash',adapter:'agnes'}},services:[{channel_id:'fixture',display_name:'验证服务',base_url:'https://apihub.agnes-ai.com/v1',api_key:'test-only-no-request'}]});
  // Re-enter to load the saved configuration; the shortcut performs no generation.
  await click('返回聊天');await until('document.querySelector("[aria-label=添加上下文]")');await read('document.querySelector("[aria-label=添加上下文]").click()');await click('生成视频');
  await until('document.querySelector("[aria-label=消息输入]")?.value.includes("咖啡店")');
  assert.ok((await read('document.querySelector("[aria-label=消息输入]").value')).startsWith('保留这条原草稿'));
  assert.equal((await rpc('media.tasks',{conversation_id:cid})).length,0);
  await click('设置');await click('多媒体');await until('document.querySelector(".media-model-table")');
  await read('document.querySelector("[aria-label=配置视频生成]").click()');
  await until('document.querySelector(".media-config-actions .primary")?.textContent.includes("去聊天使用")');
  assert.ok(await read('document.querySelector("[aria-label=配置视频生成]").textContent.includes("已配置")'));
  assert.equal(await read('document.querySelector(".media-model-table").textContent.includes("未验证")'),false);
  await snapshot('multimedia-config');
  const selectAdapter=async value=>read(`(()=>{const el=[...document.querySelectorAll('.media-binding-editor label')].find(el=>el.firstElementChild.textContent==='接入方式').querySelector('select');el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await selectAdapter('openai');await until('document.querySelector(".media-config-issue")?.textContent.includes("使用 Agnes")');
  await click('保存配置');await until('document.querySelector(".media-config-form .inline-error")');
  assert.equal((await rpc('media.config')).bindings.video_generate.adapter,'agnes');
  await snapshot('multimedia-repair');
  await click('使用 Agnes');await until('!document.querySelector(".media-config-issue") && document.querySelector(".media-config-actions .primary")?.textContent.includes("去聊天使用")');
  assert.equal(await read('document.querySelector(".media-model-table").textContent.includes("待保存")'),false);
  await read('document.querySelector("[aria-label=更多配置操作]").click()');await click('移除此项配置');
  await until('document.querySelector("[aria-label=配置视频生成]").textContent.includes("待移除")');
  assert.equal((await rpc('media.config')).bindings.video_generate.model,'agnes-video-2.5-flash');
  await click('取消修改');await until('document.querySelector(".media-config-actions .primary")?.textContent.includes("去聊天使用")');
  await rpc('themes.appearance',{value:{mode:'dark',reading:{}}});window.setSize(900,720);window.webContents.setZoomFactor(1.25);
  await until('document.documentElement.dataset.theme==="dark"');await delay(250);
  assert.ok(await read('document.documentElement.scrollWidth<=innerWidth+1'));
  await read('document.querySelector(".media-binding-editor").scrollIntoView({block:"start"})');await snapshot('multimedia-dark-small');
  assert.ok(await read('document.querySelector(".media-config-actions .primary").getBoundingClientRect().right<=innerWidth+1'));
  window.setSize(1400,930);window.webContents.setZoomFactor(1);await rpc('themes.appearance',{value:{mode:'light',reading:{}}});

  await click('去聊天使用');await until('document.querySelector("[aria-label=消息输入]")');
  for(const model of ['agnes-video-2.5-flash','agnes-image-2.5-flash','my-video-chat-model'])await rpc('settings.save_model',{channel_id:'fixture',model_name:model,name:'验证服务',base_url:'https://apihub.agnes-ai.com/v1'});
  const settings=await rpc('settings.get'),chat=settings.models.find(item=>item.model_name==='my-video-chat-model');
  await rpc('settings.select_model',{model_id:chat.id});
  await click('设置');await click('返回聊天');await until('document.querySelector("[aria-label=消息输入]")');
  await read('document.querySelector(".model-trigger").click()');await until('document.querySelector(".model-menu")');
  assert.ok(await read('document.querySelector(".model-menu .picker-list").textContent.includes("my-video-chat-model")'));
  assert.equal(await read('document.querySelector(".model-menu .picker-list").textContent.includes("agnes-video-2.5-flash")'),false);
  assert.equal(await read('document.querySelector(".model-menu .picker-list").textContent.includes("agnes-image-2.5-flash")'),false);
  await snapshot('chat-model-picker');await click('图片、视频与音频');await until('document.querySelector(".media-config-form")');
  assert.equal((await rpc('media.tasks',{conversation_id:cid})).length,0);
  await click('返回聊天');await until('document.querySelector("[aria-label=消息输入]")');
  await read('document.querySelector(".model-trigger").click()');await click('管理对话模型');
  await until('document.querySelector(".management-nav [aria-current=page]")?.textContent.includes("模型与服务")');
  await click('返回聊天');await until('document.querySelector("[aria-label=消息输入]")');
  page=await rpc('settings.page',{page:'experiments'});await rpc('settings.page_save',{page:'experiments',revision:page.revision,value:{...page.values,multimedia:false}});
  await read('document.querySelector(".model-trigger").click()');await until('document.querySelector(".model-menu")');
  await until('document.querySelectorAll(".model-media-link").length===0');
  assert.equal(errors.filter(error=>/workbench_render_failed|Uncaught|Maximum update/.test(error)).length,0,errors.join('\n'));
  clearTimeout(deadline);console.log('CODE_EDITOR_MEDIA_OK');await rpc('app.quit');
}
require('../electron/main.cjs');
