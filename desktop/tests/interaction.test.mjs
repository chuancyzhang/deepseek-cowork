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
import MessageBubble,{AssistantTurn,RunStatus} from ${source('MessageBubble.tsx')};import Composer from ${source('Composer.tsx')};
import {StoreContext} from ${source('host/context.tsx')};import {HostStore} from ${source('host/store.ts')};
import ${source('styles.css')};import ${source('management.css')};import ${source('workspace.css')};import ${source('composer.css')};
document.body.style.background='var(--bg)';
const store=new HostStore();window.replies=[];window.fail=true;window.sent=0;
window.cowork={call:async(method,p)=>{
  if(method!=='interaction.respond')throw Error(method);
  window.replies.push(p);
  if(window.fail){window.fail=false;throw Error('暂时无法提交，请重试');}
  window.lastResponse={request_id:p.request_id,status:'completed',approved:true,
    ...(Array.isArray(p.value)?{selected_options:p.value}:{answers:p.value})};
  if(window.ackMode==='held')await new Promise(resolve=>{window.releaseReply=resolve;});
  return {status:'ok',resolved:true,...(!window.ackMode&&{response:window.lastResponse})};
}};
const choice={id:'choice',question:'这批资料怎么整理？',options:[
  {value:'source',label:'按来源分组',description:'把同一来源的资料放在一起。'},
  {value:'按日期',label:'按日期排序',description:'优先查看最近更新的资料。'},
  {value:'__custom__',label:'自定义',description:'选择后填写自定义内容。'},
]};
const requests={
  multiple:{request_id:'multiple',kind:'questionnaire',title:'确认整理方式',questions:[
    choice,
    {id:'audience',question:'整理结果给谁看？',options:[{value:'team',label:'项目组成员'},{value:'partners',label:'外部合作方'}]},
    {id:'text',question:'还有哪些要求？',options:[]},
  ]},
  single:{request_id:'single',kind:'questionnaire',title:'确认整理方式',questions:[choice]},
  many:{request_id:'many',kind:'questionnaire',questions:[{id:'method',question:'这批资料按什么方式整理？',options:[
    {value:'source',label:'按来源分组',description:'保留每份资料的原始出处。'},
    {value:'topic',label:'按主题归类',description:'把相近议题的资料放在一起。'},
    {value:'date',label:'按时间排序',description:'从最近更新的资料开始。'},
    {value:'project',label:'按项目归档',description:'为每个项目整理一份目录。'},
    {value:'unique',label:'先去重再整理',description:'识别重复文件，保留一份用于汇总。'},
  ]}]},
  multi:{request_id:'multi',kind:'multi_choice',title:'需要输出哪些内容？',options:[
    {value:'index',label:'整理后的目录'},
    {value:'table',label:'资料汇总表'},
    {value:'sources',label:'原始来源'},
    {value:'unmatched',label:'未归类的资料'},
    {value:'log',label:'处理记录'},
  ]},
  checkpoint:{request_id:'checkpoint',kind:'questionnaire',title:'确认执行',metadata:{purpose:'grill_checkpoint'},
    questions:[{id:'decision',question:'现在开始执行吗？',options:[{value:'execute',label:'确认并执行'},{value:'continue',label:'继续讨论'}]}]},
};
const models=[{id:'m1',model_name:'model-one',label:'模型一',display_name:'模型一',channel_id:'c',channel_display_name:'模型服务',configured:true},{id:'m2',model_name:'model-two',label:'模型二',display_name:'模型二',channel_id:'c',channel_display_name:'模型服务',configured:true}];
function App(){
  const [text,setText]=useState(''),[model,setModel]=useState('m1'),textarea=useRef(null);
  const [questionnaire,setQuestionnaire]=useState({name:'multiple',id:'conversation',status:'running'});
  const {name,id,outcome,status,accepted}=questionnaire;
  const request=requests[name];
  const no=async()=>{};
  window.showQuestionnaire=(name,id='conversation',outcome,status='running')=>setQuestionnaire({name,id,outcome,status});
  window.finishQuestionnaire=response=>setQuestionnaire(previous=>({...previous,outcome:{answered:true,status:'completed',response}}));
  window.finishTurn=status=>setQuestionnaire(previous=>({...previous,status}));
  const runId='r',active=['sent','process','running'].includes(status);
  const questionStage={id:'step',role:'assistant',content:'我会先核对资料来源，再确认整理方式。',reasoning_content:'检查文件与来源，保留出处。',
    tool_calls:status==='process'?[{id:'python',function:{name:'run_python_code'}}]:[{id:'question',function:{name:'request_user_input'}}]};
  const messages=[questionStage];
  if(status==='process')messages.push({id:'failed',role:'tool',tool_call_id:'python',result_obj:{
    error:'Error: Python exited with code 1.\\nTraceback (most recent call last):\\nValueError: 路径不在当前目录',
  }});
  if(outcome?.answered)messages.push({id:'answer',role:'assistant',content:'已按你的回答整理资料。',created_at:1795176780});
  const questions=status==='sent'||status==='process'?[]:[{runId,callId:'question',request,outcome}];
  const timing={id:runId,start:Date.now()/1000-48,...(!active&&{end:Date.now()/1000}),thinking:{}};
  return <StoreContext.Provider value={store}><main className="conversation" style={{height:'100vh',maxWidth:900,margin:'auto',background:'var(--bg)',color:'var(--text)'}}>
    <div className="message-scroll"><div className="messages">
      <MessageBubble message={{id:'user',role:'user',content:'整理这批资料',created_at:1795176680}} cid={id} active={active} onAction={no} onFile={no}/>
      {status==='sent'?<RunStatus label="正在处理"/>:<AssistantTurn key={id+':'+request.request_id} messages={messages} cid={id}
        active={active} activeRun={active?runId:undefined} stopped={status==='stopped'} timing={timing} questions={questions}
        statusLabel={status==='running'&&!outcome&&!accepted?'等待你的回答':'正在处理'}
        onInteractionDone={()=>setQuestionnaire(previous=>previous.id===id&&previous.name===name?{...previous,accepted:true}:previous)} onAction={no} onFile={no}/>}
    </div></div>
    <Composer conversation={{id:'conversation',meta:{}}} conversations={[]} settings={{models,selected_model_id:model}} text={text} setText={setText} attachments={[]} setAttachments={()=>{}} skills={[]} active={false} sending={false} textarea={textarea} ensureConversation={async()=> 'conversation'} preferences={no} send={async()=>window.sent++} stop={no} attach={no} permission={no} selectModel={async id=>setModel(id)} route={no} browseLibrary={no} error={message=>{throw Error(message)}}/>
  </main></StoreContext.Provider>;
}
createRoot(document.getElementById('root')).render(<App/>);
`);
    await build({root:data,configFile:false,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},build:{outDir:path.join(data,'dist')}});
    await writeFile(path.join(data,'run.cjs'),`
const {app,BrowserWindow,screen}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
app.setPath('userData',path.join(__dirname,'profile'));app.commandLine.appendSwitch('disable-gpu');
const timer=setTimeout(()=>app.exit(1),35000);
app.whenReady().then(async()=>{
const native=process.env.COWORK_INTERACTION_NATIVE==='1';
const w=new BrowserWindow({show:false,width:900,height:840,useContentSize:true,webPreferences:{offscreen:!native,backgroundThrottling:false}});
const errors=[];w.webContents.on('console-message',d=>{if(d.level==='error')errors.push(d.message);});
const read=s=>w.webContents.executeJavaScript(s);const until=async s=>{for(let i=0;i<150;i++){if(await read(s))return;await new Promise(r=>setTimeout(r,20));}throw Error(s+' '+errors.join(';'));};
const click=async s=>{await read(s+'.click()');await new Promise(r=>setTimeout(r,30));};
const input=async(s,value)=>{await read('(()=>{const el='+s+';Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(el,'+JSON.stringify(value)+');el.dispatchEvent(new Event("input",{bubbles:true}));})()');};
const answer='document.querySelector(".custom-answer")';
const custom='document.querySelector(".question-custom[data-selected]")';
const submit='document.querySelector(".questionnaire-footer button")';
const receipt='document.querySelector(".interaction-receipt")';
const bubbles='document.querySelector(".interaction-answers")';
const processSummary='document.querySelector("button.process-summary")';
const snap=async(name,selector)=>{
  const output=process.env.COWORK_INTERACTION_SCREENSHOTS;
  if(!output)return;
  await new Promise(r=>setTimeout(r,200));
  const bounds=await read('(()=>{const r=document.querySelector('+JSON.stringify(selector)+').getBoundingClientRect();return {x:r.left,y:r.top,width:r.width,height:r.height};})()');
  const zoom=w.webContents.getZoomFactor();
  const rect={x:Math.round((bounds.x-8)*zoom),y:Math.round((bounds.y-8)*zoom),
    width:Math.ceil((bounds.width+16)*zoom),height:Math.ceil((bounds.height+16)*zoom)};
  await fs.mkdir(output,{recursive:true});
  await fs.writeFile(path.join(output,name+'.png'),(await w.webContents.capturePage(rect)).toPNG());
};
await w.loadFile(path.join(__dirname,'dist/index.html'));
if(native)w.showInactive();
await until('Boolean(document.querySelector(".interaction"))');
await read('window.showQuestionnaire("single","preview",undefined,"sent")');
await until('document.querySelector(".run-state")?.textContent==="正在处理"');
assert.equal(await read('Boolean(document.querySelector(".process-summary"))'),false);
await read('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
const sentLayout=await read('(()=>{const body=document.querySelector(".message.user .message-body").getBoundingClientRect();const footer=document.querySelector(".message.user .message-footer").getBoundingClientRect();const status=document.querySelector(".run-state").getBoundingClientRect();return {footerGap:footer.top-body.bottom,rightOffset:footer.right-body.right,statusGap:status.top-footer.bottom};})()');
assert.ok(sentLayout.footerGap>=0&&sentLayout.footerGap<=6&&Math.abs(sentLayout.rightOffset)<=1,JSON.stringify(sentLayout));
assert.ok(sentLayout.statusGap>=0&&sentLayout.statusGap<=16,JSON.stringify(sentLayout));
await read('window.showQuestionnaire("single","preview",undefined,"process")');
await until('Boolean('+processSummary+')');
assert.ok(await read(processSummary+'.querySelector(".spin")!==null && '+processSummary+'.querySelector(".sr-only").textContent==="正在处理"'));
assert.equal(await read('document.querySelector(".process-tool-error").textContent'),'ValueError: 路径不在当前目录');
assert.match(await read('document.querySelector(".process-tool-name").textContent'),/run_python_code/);
await read('window.showQuestionnaire("multiple")');
await until('Boolean(document.querySelector(".interaction"))');
assert.equal(await read('document.querySelector(".question-block input[type=radio]").checked'),true);
assert.equal(await read(answer+'.value'),'');
await read('document.querySelector(".question-block input").dispatchEvent(new KeyboardEvent("keydown",{key:"2",bubbles:true}))');
assert.equal(await read('document.querySelectorAll(".question-block input[type=radio]")[1].checked'),true);
assert.equal(await read('window.replies.length'),0);
await click('document.querySelector(".question-heading")');
assert.equal(await read('document.querySelector(".question-heading small").textContent'),'按日期排序');
await click('document.querySelector(".question-heading")');
await input(answer,' ');
await input(answer,'');
await click(submit);
assert.match(await read('document.querySelector(".interaction [role=alert]").textContent'),/第 1 项/);
assert.equal(await read('window.replies.length'),0);
await until('document.activeElement==='+answer);
await input(answer,'按日期');
assert.equal(await read('Boolean('+custom+')'),true);
w.setContentSize(640,650);w.webContents.setZoomFactor(1.25);
await until('innerWidth<600').catch(async error=>{
  throw Error(error.message+' '+JSON.stringify({window:w.getContentSize(),zoom:w.webContents.getZoomFactor(),viewport:await read('[innerWidth,innerHeight]')}));
});
await click('document.querySelectorAll(".question-heading")[1]');
await click('document.querySelectorAll(".question-block input[type=radio]")[1]');
await click(submit);
assert.match(await read('document.querySelector(".interaction [role=alert]").textContent'),/第 3 项/);
await until('document.activeElement==='+answer);
const smallLayout=await read('({submitBottom:'+submit+'.getBoundingClientRect().bottom,viewportBottom:document.querySelector(".message-scroll").getBoundingClientRect().bottom})');
assert.ok(smallLayout.submitBottom<=smallLayout.viewportBottom+1,JSON.stringify(smallLayout));
await input(answer,'保留原始出处');
assert.equal(await read('document.querySelector(".questionnaire-footer > span").textContent'),'已填写 3 / 3 项');
w.setContentSize(900,840);w.webContents.setZoomFactor(1);
await until('innerWidth>700');
await click('document.querySelector(".question-heading")');
assert.equal(await read(answer+'.value'),'按日期');
assert.equal(await read('Boolean('+custom+')'),true);
assert.equal(await read('document.querySelectorAll(".question-heading small")[0].textContent'),'外部合作方');
await snap('questionnaire-multiple','.interaction');
await click('document.querySelectorAll(".question-heading")[2]');
assert.equal(await read(answer+'.value'),'保留原始出处');
await click(submit);
await until('document.querySelector(".interaction [role=alert]")?.textContent.includes("重试")');
assert.equal(await read(answer+'.value'),'保留原始出处');
assert.deepEqual(await read('window.replies[0].value'),{
  choice:{text:'按日期'},audience:{selected_options:['partners']},text:{text:'保留原始出处'},
});
await click(submit);await until('Boolean('+bubbles+')');
assert.deepEqual(await read('[...document.querySelectorAll(".interaction-answer-value")].map(p=>p.textContent)'),['按日期','外部合作方','保留原始出处']);
assert.equal(await read('document.querySelectorAll(".message.user").length'),1);
assert.ok(await read('Math.abs(document.querySelector(".interaction-answer .message-body").getBoundingClientRect().right-document.querySelector(".process-timeline").getBoundingClientRect().right)<1'));

await read('window.showQuestionnaire("single");window.ackMode="legacy"');
await until('document.querySelector(".interaction-title")?.textContent==="这批资料怎么整理？"');
assert.equal(await read('document.querySelector(".question-heading")===null'),true);
const customText='先按来源分组，同组内按日期排序。';
await input(answer,customText);
await snap('questionnaire','.interaction');
await click(submit);await until('Boolean('+receipt+')');
await click(receipt+'.querySelector("summary")');
assert.match(await read(receipt+'.textContent'),/正在读取提交的回答/);
await read('window.finishQuestionnaire(window.lastResponse)');
await until('Boolean('+bubbles+')');
assert.equal(await read('document.querySelector(".interaction-answer-value").textContent'),customText);
assert.equal(await read('document.querySelector(".interaction-answer-question").textContent'),'这批资料怎么整理？');
await snap('questionnaire-receipt','.assistant-turn');
await click(processSummary);
await until('!'+bubbles);
assert.match(await read('document.querySelector(".message.assistant .message-body").textContent'),/已按你的回答整理资料/);
await click(processSummary);await until('Boolean('+bubbles+')');
const outcome={answered:true,status:'completed',response:await read('window.lastResponse')};
const reloaded=new Promise(resolve=>w.webContents.once('did-finish-load',resolve));
w.reload();
await reloaded;
await until('typeof window.showQuestionnaire==="function"');
await read('window.showQuestionnaire("single","conversation",'+JSON.stringify(outcome)+',"completed")');
await until(processSummary+'?.getAttribute("aria-expanded")==="false"');
assert.match(await read(processSummary+'.textContent'),/^用时 /);
assert.equal(await read('Boolean('+bubbles+')'),false);
await click(processSummary);await until('Boolean('+bubbles+')');
assert.equal(await read('document.querySelector(".interaction-answer-value").textContent'),customText);
assert.equal(await read('document.querySelectorAll(".message.assistant .message-actions button").length'),4);

await read('window.showQuestionnaire("checkpoint")');
await until('document.querySelector(".interaction-title")?.textContent==="现在开始执行吗？"');
assert.equal(await read('document.querySelector(".question-block input:checked")===null'),true);
assert.doesNotMatch(await read('document.querySelector(".question-block").textContent'),/推荐/);

w.setContentSize(640,800);w.webContents.setZoomFactor(1.25);
await read('document.documentElement.dataset.theme="dark";window.fail=false;window.ackMode="held"');
await read('localStorage.setItem("cowork-questionnaire:other:single",JSON.stringify({answers:{choice:"__custom__"},text:"",selections:[]}));window.showQuestionnaire("single","other")');
await until('Boolean('+custom+')');
assert.equal(await read(answer+'.value'),'');
await input(answer,'只核对更新日期');
const geometry=await read('(()=>{const field=document.querySelector(".custom-answer").getBoundingClientRect();const card=document.querySelector(".interaction").getBoundingClientRect();return {fits:field.left>=card.left&&field.right<=card.right,bottom:document.querySelector(".questionnaire-footer button").getBoundingClientRect().bottom,height:innerHeight};})()');
assert.ok(geometry.fits&&geometry.bottom<=geometry.height,JSON.stringify(geometry));
await snap('questionnaire-dark','.interaction');
await click(submit);await until('Boolean(window.releaseReply)');
assert.equal(await read(submit+'.disabled'),true);
await read('window.finishQuestionnaire(window.lastResponse)');
await until('Boolean('+bubbles+')');
await read('window.releaseReply()');
assert.equal(await read('document.querySelector(".interaction-answer-value").textContent'),'只核对更新日期');
assert.equal(await read('document.querySelectorAll(".interaction-answers").length'),1);

await read('window.showQuestionnaire("single","stopped")');
await until('Boolean('+answer+')');
await input(answer,'保留未提交的草稿');
await read('window.finishTurn("stopped")');
await until(processSummary+'?.textContent.includes("已停止")');
await click(processSummary);
await until('Boolean('+receipt+')');
assert.equal(await read('Boolean('+bubbles+')'),false);
await click(receipt+'.querySelector("summary")');
assert.match(await read(receipt+'.textContent'),/回答未提交.*保留未提交的草稿/);

w.setContentSize(900,840);w.webContents.setZoomFactor(1);
await read('document.documentElement.dataset.theme="light";window.fail=false;window.ackMode=undefined;window.showQuestionnaire("many","five-options")');
await until('document.querySelectorAll(".question-block input[type=radio]").length===5');
await read('document.querySelector(".question-block input").dispatchEvent(new KeyboardEvent("keydown",{key:"4",bubbles:true}))');
assert.equal(await read('document.querySelectorAll(".question-block input[type=radio]")[3].checked'),true);
await snap('questionnaire-five-options','.interaction');
await click(submit);await until('Boolean('+bubbles+')');
assert.equal(await read('document.querySelector(".interaction-answer-value").textContent'),'按项目归档');

await read('window.showQuestionnaire("multi","multiple-options")');
await until('document.querySelectorAll(".choice-options input[type=checkbox]").length===5');
await click('document.querySelectorAll(".choice-options input")[0]');
await click('document.querySelectorAll(".choice-options input")[2]');
assert.equal(await read('document.querySelectorAll(".choice-options input:checked").length'),2);
await snap('questionnaire-multi-select','.interaction');
await click(submit);await until('Boolean('+bubbles+')');
assert.deepEqual(await read('window.replies.at(-1).value'),['index','sources']);
assert.equal(await read('document.querySelector(".interaction-answer-question").textContent'),'需要输出哪些内容？');
assert.equal(await read('document.querySelector(".interaction-answer-value").textContent'),'整理后的目录、原始来源');

await read('window.showQuestionnaire("multi","multiple-history",{answered:true,response:{selected_options:["index","sources"]}},"completed")');
await until(processSummary+'?.getAttribute("aria-expanded")==="false"');
assert.equal(await read('Boolean('+bubbles+')'),false);
await click(processSummary);
await until('Boolean('+bubbles+')');
assert.equal(await read('document.querySelector(".interaction-answer-value").textContent'),'整理后的目录、原始来源');

await read('window.showQuestionnaire("multi","multiple-stopped")');
await until('document.querySelectorAll(".choice-options input[type=checkbox]").length===5');
await click('document.querySelectorAll(".choice-options input")[0]');
await click('document.querySelectorAll(".choice-options input")[2]');
await read('window.finishTurn("stopped")');
await until(processSummary+'?.textContent.includes("已停止")');
await click(processSummary);
await until('Boolean('+receipt+')');
await click(receipt+'.querySelector("summary")');
assert.match(await read(receipt+'.textContent'),/回答未提交.*整理后的目录、原始来源/);

await click('document.querySelector(".model-trigger")');await until('Boolean(document.querySelector(".model-menu"))');
assert.equal(await read('document.querySelector(".model-menu").textContent.includes("用途待")'),false);
await read('document.querySelector("textarea").dispatchEvent(new PointerEvent("pointerdown",{bubbles:true}));document.querySelector("textarea").focus()');await until('!document.querySelector(".model-menu")');
await read('document.querySelector("textarea").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');await until('window.sent===1');
await click('document.querySelector(".model-trigger")');await click('[...document.querySelectorAll(".model-menu .picker-row")].find(el=>el.textContent==="模型二")');await until('!document.querySelector(".model-menu")');
assert.equal(errors.length,0,errors.join(';'));clearTimeout(timer);
console.log('INTERACTION_OK',JSON.stringify({native,size:w.getContentSize(),displayScale:screen.getDisplayMatching(w.getBounds()).scaleFactor,zoom:w.webContents.getZoomFactor()}));
app.exit(0);
}).catch(e=>{console.error(e.stack);app.exit(1);});
`);
    const env={...process.env,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(data,'run.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',v=>output+=v);child.stderr.on('data',v=>output+=v);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/INTERACTION_OK/);
    console.log(output.match(/INTERACTION_OK[^\r\n]*/)?.[0]);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
