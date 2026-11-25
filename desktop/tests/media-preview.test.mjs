import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('local media and background video tasks play in chat without duplicate players',{timeout:45000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/media-preview-tests');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try {
    const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useMemo,useState,useSyncExternalStore} from 'react';
import {createRoot} from 'react-dom/client';
import MessageBubble,{AssistantTurn} from ${modulePath('MessageBubble.tsx')};
import FilePreview from ${modulePath('FilePreview.tsx')};
import {VideoTasks} from ${modulePath('plugins/multimedia/ChatTasks.tsx')};
import {StoreContext} from ${modulePath('host/context.tsx')};
import {HostStore} from ${modulePath('host/store.ts')};
import {receiveEvents} from ${modulePath('host/conversations.ts')};
import {readVideoTaskLayout,videoPathKey} from ${modulePath('host/videoTasks.ts')};
import {messageGroups} from ${modulePath('runtime.ts')};
import ${modulePath('styles.css')};
const store=new HostStore();
window.deliverTask=task=>receiveEvents(store,[{event:'media_task',conversation_id:task.conversation_id,data:task}]);
const answer = (id, files) => ({id,role:'assistant',content:files.map(file=>'['+file.name+'](<'+file.path+'>)').join('\\n'),
  file_references:files.map(file=>({path:file.path,name:file.name,kind:file.payload.preview_kind,references:[file.path]}))});
function VideoConversation({cid,files,openFile,earlier}) {
  const groups = useMemo(() => {
    const segment = (id, prompt, file) => [
      {id:id+'-request',role:'user',content:prompt},
      {id:id+'-call',role:'assistant',content:'',meta:{turn_id:'run'},tool_calls:[{id,function:{name:'media_create'}}]},
      {...answer(id+'-reply',file ? [file] : []),...(file ? {} : {content:'第二段已提交。'}),meta:{turn_id:'run'}},
    ];
    return messageGroups([
      ...(earlier ? segment('first','生成第一段日落视频',files[1]) : []),
      ...segment('second','继续生成第二段，镜头拉近'),
      {id:'normal-request',role:'user',content:'还有哪些地方适合取景？'},
      {id:'normal-reply',role:'assistant',content:'可以沿江拍摄。',meta:{turn_id:'normal-run'}},
    ]);
  },[earlier,files]);
  const readLayout = useMemo(() => readVideoTaskLayout(store,cid,groups),[cid,groups]);
  const layout = useSyncExternalStore(listener=>store.subscribe(store.sessionKey('media_tasks',cid),listener),readLayout);
  const fileAction = path => {
    const owner = layout.files.get(videoPathKey(path));
    if (owner) {
      window.linkedVideo = owner.taskId;
      store.set(store.sessionKey('videoPreview:'+owner.taskId,cid),owner.path);
    }
    else openFile(path);
  };
  const cards = ids => ids?.length ? <VideoTasks ids={ids} cid={cid} videoFiles={layout.files} openFile={openFile} configured={[]} configure={()=>{}}/> : null;
  return <><div className="messages">{groups.map(group => group.assistant
    ? <AssistantTurn key={group.id} messages={group.messages} cid={cid} active={false} onAction={()=>{}} onFile={fileAction}
      videoFiles={layout.files} videoTasks={cards(layout.groups.get(group.id))}/>
    : <MessageBubble key={group.id} message={group.messages[0]} cid={cid} active={false} onAction={()=>{}} onFile={fileAction}/> )}</div>
    <aside aria-label="任务">{cards(layout.unplaced)}</aside></>;
}
function Fixture(){const [files,setFiles]=useState([]),[workspace,setWorkspace]=useState(null);
const [cid,setCid]=useState('one');window.selectConversation=setCid;
const [videoMode,setVideoMode]=useState(false),[earlier,setEarlier]=useState(false);
window.showVideoConversation=()=>setVideoMode(true);window.showEarlier=setEarlier;
window.deliver=setFiles;
const openFile=path=>setWorkspace(files.find(file=>file.path===path));
return <StoreContext.Provider value={store}>{cid==='one'&&(videoMode && files.length===3
  ? <VideoConversation cid={cid} files={files} openFile={openFile} earlier={earlier}/>
  : <MessageBubble cid={cid} active={false} message={answer('answer',files)} onAction={()=>{}} onFile={openFile}/>)}
{workspace&&<FilePreview key={workspace.id} file={workspace} text="" cid="one" change={()=>{}} register={()=>{}}/>}</StoreContext.Provider>;}
createRoot(document.getElementById('root')).render(<Fixture/>);`);
    const html=(await readFile(path.join(desktop,'index.html'),'utf8')).replace('/src/main.tsx','./entry.tsx');
    await writeFile(path.join(data,'index.html'),html);
    await build({configFile:false,root:data,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist'),rollupOptions:{input:path.join(data,'index.html')}}});
    const env={...process.env,COWORK_MEDIA_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'media-preview.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/MEDIA_PREVIEW_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
