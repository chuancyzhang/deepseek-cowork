import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));
test('Mermaid renders streamed and historic Markdown with isolated themes, recovery and complete source',{timeout:120000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/mermaid-tests');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try {
    const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Markdown} from ${modulePath('MessageBubble.tsx')};
import ${modulePath('styles.css')};
window.cowork={call:async(method,params)=>{if(method==='clipboard.write'){window.copied=params.text;return;}throw Error(method);}};
function Fixture(){const [value,setValue]=useState({text:'',streaming:false,id:'one'});window.deliver=setValue;
return <main style={{maxWidth:880,padding:24,margin:'auto'}}><h2>应用架构</h2><div className="markdown"><Markdown key={value.id} text={value.text} streaming={value.streaming}/></div></main>;}
createRoot(document.getElementById('root')).render(<Fixture/>);`);
    const html=(await readFile(path.join(desktop,'index.html'),'utf8')).replace('/src/main.tsx','./entry.tsx');
    await writeFile(path.join(data,'index.html'),html);
    await build({configFile:false,root:data,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist'),rollupOptions:{input:path.join(data,'index.html')}}});
    const env={...process.env,COWORK_MERMAID_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'mermaid.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/MERMAID_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
