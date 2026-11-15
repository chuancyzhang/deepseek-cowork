import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('local media plays in chat and workspace under the application CSP, including reload and seeking',{timeout:40000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/media-preview-tests');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try {
    const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import MessageBubble from ${modulePath('MessageBubble.tsx')};
import FilePreview from ${modulePath('FilePreview.tsx')};
import {StoreContext} from ${modulePath('host/context.tsx')};
import {HostStore} from ${modulePath('host/store.ts')};
import ${modulePath('styles.css')};
const store=new HostStore();
window.cowork={call:async(method,params)=>{if(method==='file.open'){if(params.conversation_id!=='one')throw Error('wrong conversation');return window.mediaFiles.find(file=>file.path===params.path);}throw Error(method);}};
function Fixture(){const [files,setFiles]=useState([]),[workspace,setWorkspace]=useState(null);
window.deliver=value=>{window.mediaFiles=value;setFiles(value);};
const refs=files.map(file=>({path:file.path,name:file.name,kind:file.payload.preview_kind,references:[file.path]}));
return <StoreContext.Provider value={store}><MessageBubble cid="one" active={false} message={{id:'answer',role:'assistant',content:files.map(file=>'['+file.name+'](<'+file.path+'>)').join('\\n'),file_references:refs}} onAction={()=>{}} onFile={path=>setWorkspace(files.find(file=>file.path===path))}/>
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
