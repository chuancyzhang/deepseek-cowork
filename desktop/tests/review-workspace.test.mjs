import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('review editing, recovery and library remain conversation-owned in the real renderer',{timeout:60000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/review-workspace-tests');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try {
    const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import Workspace,{emptyWorkspace} from ${modulePath('Workspace.tsx')};
import Composer from ${modulePath('Composer.tsx')};
import Library from ${modulePath('management/Library.tsx')};
import {StoreContext,useHostField} from ${modulePath('host/context.tsx')};
import {HostStore} from ${modulePath('host/store.ts')};
import {receiveEvents} from ${modulePath('host/conversations.ts')};
import {AssistantTurn,GenerationTaskStatus} from ${modulePath('MessageBubble.tsx')};
import {activeReview} from ${modulePath('reviews.ts')};
import ${modulePath('styles.css')};
import ${modulePath('management.css')};
import ${modulePath('workspace.css')};
import ${modulePath('composer.css')};
import ${modulePath('plugins/plugins.css')};
document.documentElement.style.setProperty('--font-family','"Microsoft YaHei UI",sans-serif');
const store=new HostStore(),listeners=new Set(),clone=value=>JSON.parse(JSON.stringify(value));
const entries={a:{},b:{}};
const description='分析访谈与原始资料，逐条保留依据，区分事实和推测，标记样本限制并整理可复用方法。'.repeat(5)+'结尾关键词';
const skills=[{name:'research',display_name:'资料核对',description},{name:'mcp-server-docs',display_name:'文档服务',description}];
window.calls=[];
window.emit=event=>{receiveEvents(store,[event]);listeners.forEach(listener=>listener([event]));};
window.createReview=(kind='skill')=>{const id=kind;entries.a[id]={id,kind,title:kind==='skill'?'访谈证据核对':'访谈分析报告',format:'docx',scope:'task',source:[],source_ids:[],target_skill:'',revision:0,status:kind==='skill'?'ready':'setup',content:kind==='skill'?{skill_name:'interview-evidence',description:'从访谈资料中归纳可复用方法，并保留原始依据。',instructions_md:'## 使用步骤\\n1. 核对原始记录与来源。\\n2. 按主题归纳证据。\\n3. 将事实与推测分开。\\n\\n## 验证与边界\\n逐条回溯引用，标记样本与时间范围。',resources:[{kind:'asset',path:'assets/访谈记录模板.docx',description:'保留参考原件的结构与样式',size:18432,sha256:'a'.repeat(64),selected:true},{path:'references/checklist.md',content:'检查来源、时间、样本与反例。',selected:true}]}:{}};window.emit({event:'review_changed',conversation_id:'a',data:{...clone(entries.a[id]),open:true}});};
window.cowork={onEvents:listener=>{listeners.add(listener);return()=>listeners.delete(listener);},call:async(method,p={})=>{
  window.calls.push({method,p:clone(p)});
  if(method==='skills.list')return skills;
  if(method==='skills.inventory')return [{name:'existing-method',display_name:'既有核对流程',editable:true}];
  if(method==='experts.list')return [{id:'analyst',name:'分析专家',description}];
  if(method==='workbench.list')return {tabs:[]};
  if(method==='workbench.profiles')return [{id:'powershell',name:'PowerShell'}];
  if(method==='library.catalog')return {public:{id:'public',name:'公共资料'},mine:[{id:'project',name:'客户研究'}]};
  if(method==='library.children')return {items:[{id:'notes',collection_id:'project',name:'访谈记录.md',relative:'访谈记录.md',project:'客户研究'}],total:1};
  if(method==='library.reference')return {path:'D:/projects/research/访谈记录.md',name:'访谈记录.md'};
  if(method==='reviews.list')return clone(Object.values(entries[p.conversation_id]));
  if(method==='reviews.get') {const result=clone(entries[p.conversation_id][p.id]);if(window.delayGet)await new Promise(resolve=>window.releaseGet=resolve);return result;}
  const entry=entries[p.conversation_id]?.[p.id];
  if(method==='reviews.update') {if(p.revision!==entry.revision)throw Error('草稿已更新');Object.assign(entry,clone(p.value),{revision:entry.revision+1});if(p.value.target_skill)entry.target_snapshot={body:'## 已有方法\\n核对资料完整性。'};return clone(entry);}
  if(method==='reviews.save') {if(window.failSave){window.failSave=false;throw Error('保存未完成，草稿已保留，请重试。');}entry.status='saved';entry.saved_name=entry.content.skill_name;entry.saved_path='我的技能/'+entry.saved_name;entry.revision++;return clone(entry);}
  if(method==='reviews.generate')throw Error('请先配置模型，当前要求已保留。');
  throw Error(method);
}};
function Fixture(){const [cid,setCid]=useState('a'),[views,setViews]=useState(()=>JSON.parse(localStorage.getItem('views')||'{}')),[preferences,setPreferences]=useState({}),[attachments,setAttachments]=useState([]),[text,setText]=useState('保留我的下一条输入'),[dark,setDark]=useState(false);
  const value=views[cid]||emptyWorkspace(),textarea=useRef(null),meta=preferences[cid]||{},[reviews]=useHostField(store.sessionKey('reviews',cid),[]),conversation={id:cid,title:'客户访谈整理',meta,run:null,reviews};
  const change=patch=>setViews(previous=>{const next={...previous,[cid]:{...(previous[cid]||emptyWorkspace()),...patch}};localStorage.setItem('views',JSON.stringify(next));return next;});
  const openReview=id=>change({selected:'review:'+id,reviewOpen:[...new Set([...(value.reviewOpen||[]),id])],reviewClosed:(value.reviewClosed||[]).filter(item=>item!==id)});
  const no=async()=>{},error=message=>{throw Error(message);};
  return <StoreContext.Provider value={store}><div className={'fixture '+(dark?'dark':'')} style={{'--workspace-width':'46%',display:'flex',height:'100vh',background:'var(--bg)',color:'var(--text)'}}><main style={{flex:1,minWidth:0,display:'flex',flexDirection:'column',padding:24}}>
    <h2>客户访谈整理</h2><p>已核对来源并归纳结论，可以继续审阅成果。</p><div className="button-row"><button id="switch" onClick={()=>setCid(cid==='a'?'b':'a')}>切换会话</button><button id="theme" onClick={()=>{setDark(!dark);document.documentElement.dataset.theme=dark?'light':'dark';}}>切换主题</button><button id="reopen" onClick={()=>openReview('skill')}>打开审阅</button></div><div style={{flex:1}}/>
    <Composer conversation={conversation} conversations={[]} settings={{models:[]}} text={text} setText={setText} attachments={attachments} setAttachments={setAttachments} skills={meta.selected_skill_names||[]} active={false} sending={false} textarea={textarea} ensureConversation={async()=>cid} preferences={async patch=>setPreferences(previous=>({...previous,[cid]:{...meta,...patch}}))} send={no} stop={no} attach={no} permission={no} selectModel={no} route={no} error={error} browseLibrary={async()=>change({libraryOpen:true,selected:'library'})}/>
  </main><Workspace key={cid} conversation={conversation} value={value} change={change} expanded={false} expand={()=>{}} close={()=>{}} obscured={false} dark={dark} editors={[]} fileTree={{files:[]}} fileContent={null} libraryContent={<Library key={cid} scope={'library:'+cid} projectsKey="fixture" projectsChanged={no} onError={error} onReference={async (source,items)=>{const ref=await window.cowork.call("library.reference",{source,...items[0]});setAttachments(previous=>[...previous,ref]);}} confirm={async()=>1} onDirty={()=>{}}/>} selectFile={no} openFile={no} closeFile={no} refreshFiles={no} createDocument={no} preserve={no} openReview={openReview} registerEditorFlush={()=>{}} error={error} confirm={async()=>1}/></div></StoreContext.Provider>;
}
function TaskPreview(){
  const [state,setState]=useState(null);window.taskPreview=setState;
  if(!state)return <Fixture/>;
  const cid=state.cid||'a',status=state.status||'running',id='generation',run={id,conversation_id:'a',status};
  const task={id:'draft',kind:state.kind||'office',format:state.format||'docx',title:state.title||'办公稿',status:state.ready?'ready':'generating',revision:1,run_id:id};
  const active=['running','queued','stopping'].includes(status),selected=activeReview([task],run,cid);
  const timing={id,thinking:{},start:Date.now()/1000-33,...(!active?{end:Date.now()/1000}:{})};
  const messages=[{id:'step',role:'assistant',reasoning_content:'核对来源，保留事实与限制。',tool_calls:[{id:'read',function:{name:'read_file',arguments:'{}'}}],meta:{turn_id:id}},{id:'answer',role:'assistant',content:'已整理的内容始终保留。办公稿.docx',live:active,meta:{turn_id:id},file_references:!active?[{url:'cowork-file:办公稿.docx',path:'办公稿.docx',name:'办公稿.docx',kind:'document',label:'DOCX',references:['办公稿.docx']}]:[]}];
  const questions=state.pending?[{sourceId:'step',runId:id,request:{request_id:'q',kind:'questionnaire',questions:[{id:'audience',question:'文稿用于哪类场合？',options:[{value:'internal',label:'内部交流'},{value:'external',label:'对外汇报'}]}]}}]:[];
  return <StoreContext.Provider value={store}><main className="task-fixture" style={{maxWidth:760,padding:24,margin:'0 auto'}}><h2>工程经验与方法整理</h2><div className="message user"><div className="message-body">将本轮回答整理为草稿，供我审阅。</div></div>{state.waiting?<GenerationTaskStatus task={task} waiting/>:<AssistantTurn key={cid} messages={messages} cid={cid} active={active} activeRun={selected?run.id:undefined} reviewTask={selected} timing={timing} stopped={status==='interrupted'} statusLabel={status==='stopping'?'正在停止，保留已完成内容…':state.pending?'等待你的回答':'正在处理'} questions={questions} interactionRun={id} onAction={()=>{}} onFile={()=>{}}/>}<textarea aria-label="保留的输入" defaultValue="下一步补充要求"/></main></StoreContext.Provider>;
}
createRoot(document.getElementById('root')).render(<StoreContext.Provider value={store}><TaskPreview/></StoreContext.Provider>);`);
    await writeFile(path.join(data,'index.html'),'<meta charset="utf-8"><div id="root"></div><script type="module" src="./entry.tsx"></script>');
    await build({configFile:false,root:data,base:'./',logLevel:'silent',plugins:[{
      name:'review-fixture-host',enforce:'pre',
      load(id){if(id.replaceAll('\\','/')===path.join(desktop,'src/host/Host.tsx').replaceAll('\\','/'))return `
        import {useHost} from ${modulePath('host/context.tsx')};
        export function useWorkbench(){return {host:useHost(),addProject:async()=>{throw Error('Project creation is outside this fixture');}};}
        export function useOptionalWorkbench(){return null;}
      `;},
    }],resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist'),rollupOptions:{input:path.join(data,'index.html')}}});
    const env={...process.env,COWORK_REVIEW_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'review-workspace.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/REVIEW_WORKSPACE_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
