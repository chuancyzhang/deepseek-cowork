import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));
const {SnippetHost}=require('../electron/snippets.cjs');
test('snippet resources are owned, bounded, released and never logged as source',()=>{
  const diagnostics=[],host=new SnippetHost({diagnostic:(...args)=>diagnostics.push(args)}),owner={};
  const run=host.create(owner,{source:'<b>PRIVATE_SOURCE</b>',kind:'html',scope:'chat:message:1'});
  assert.throws(()=>host.start({},run.token),/失效/);
  assert.throws(()=>host.release({},run.token),/失效/);
  assert.throws(()=>host.create(owner,{source:'x'.repeat(262145),kind:'html',scope:'one'}),/256 KB/);
  assert.throws(()=>host.create(owner,{source:'require("fs")',kind:'node',scope:'one'}),/浏览器/);
  assert.ok(host.response(new URL(run.url)).headers.get('Content-Security-Policy').includes("connect-src 'none'"));
  host.release(owner,run.token);assert.equal(host.pages.size,0);
  assert.equal(host.response(new URL(run.url)).status,404);
  assert.ok(!JSON.stringify(diagnostics).includes('PRIVATE_SOURCE'));
});
test('real Electron snippets: intentional execution, interaction, lifecycle, isolation and recovery',{timeout:120000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/snippet-tests');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try {
    const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Markdown} from ${modulePath('MessageBubble.tsx')};
import ${modulePath('styles.css')};
function Fixture(){const [value,setValue]=useState({text:'',streaming:false,id:'one'});window.deliver=setValue;
return <main style={{maxWidth:880,padding:24,margin:'auto'}}><h2>代码片段预览</h2><div className="markdown"><Markdown text={value.text} streaming={value.streaming} previewScope={value.id}/></div></main>;}
createRoot(document.getElementById('root')).render(<Fixture/>);`);
    const html=(await readFile(path.join(desktop,'index.html'),'utf8')).replace('/src/main.tsx','./entry.tsx');
    await writeFile(path.join(data,'index.html'),html);
    await writeFile(path.join(data,'preload.cjs'),`const {contextBridge,ipcRenderer}=require('electron');contextBridge.exposeInMainWorld('cowork',{call:(method,params)=>ipcRenderer.invoke('test-call',method,params),onEvents:callback=>{const listener=(_,events)=>callback(events);ipcRenderer.on('cowork:events',listener);return()=>ipcRenderer.removeListener('cowork:events',listener);}});`);
    await build({configFile:false,root:data,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist')}});
    const env={...process.env,COWORK_SNIPPET_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'snippets.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/SNIPPETS_FIXTURE_OK/);console.log(output.trim());
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
