import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';

test('update preparation is visible and cancellable in chat and settings, including a narrow dark window',{timeout:60000},async()=>{
  const require=createRequire(import.meta.url),desktop=fileURLToPath(new URL('../',import.meta.url));
  const qa=path.resolve(desktop,'../.tmp/update-lifecycle-fix/ui');await fs.mkdir(qa,{recursive:true});
  const data=await fs.mkdtemp(path.join(qa,'case-'));
  const modulePath=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
  try{
    await fs.writeFile(path.join(data,'entry.tsx'),`
import React from 'react';import {createRoot} from 'react-dom/client';
import Updates,{UpdateNotice} from ${modulePath('management/Updates.tsx')};
import {StoreContext} from ${modulePath('host/context.tsx')};import {HostStore} from ${modulePath('host/store.ts')};
import ${modulePath('styles.css')};import ${modulePath('management.css')};
const store=new HostStore();window.calls=[];
const initial={revision:'1',phase:'planning',message:'正在准备安装…',busy:true,percent:-1,current_version:'5.4.0',version:'5.4.1',ready:false,can_install:false,notice:false,identity:'fixture',source:'local',reinstall:false,notes:'',packaged:true,local_manifest_verified:true,install:{id:'a'.repeat(32),phase:'preparing',error:''},can_cancel_install:true};
window.setUpdate=patch=>store.set('updates',{...store.get('updates'),...patch});store.set('updates',initial);
window.cowork={call:async(method)=>{window.calls.push(method);if(method==='updates.cancel'){const next={...store.get('updates'),revision:'2',can_cancel_install:false,install:{...initial.install,phase:'cancelling',error:'正在取消安装准备…'}};store.set('updates',next);return next;}throw Error(method);}};
createRoot(document.getElementById('root')).render(<StoreContext.Provider value={store}><div style={{display:'flex',height:'100vh',background:'var(--bg)',color:'var(--text)'}}><aside style={{width:240,flexShrink:0,display:'flex',flexDirection:'column',padding:16,borderRight:'1px solid var(--border)'}}><strong>DeepSeek Cowork</strong><div style={{flex:1}}/><UpdateNotice/><p>设置</p></aside><main style={{padding:24,flex:1,minWidth:0,overflow:'auto'}}><h2>更新与关于</h2><Updates/></main></div></StoreContext.Provider>);
`);
    await fs.writeFile(path.join(data,'index.html'),'<meta charset="utf-8"><div id="root"></div><script type="module" src="./entry.tsx"></script>');
    await build({configFile:false,root:data,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist')}});
    await fs.writeFile(path.join(data,'fixture.cjs'),`
const {app,BrowserWindow}=require('electron'),assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
app.setPath('userData',path.join(__dirname,'profile'));let window;const deadline=setTimeout(()=>app.exit(1),25000);
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
app.whenReady().then(async()=>{
 window=new BrowserWindow({show:false,width:1100,height:780,webPreferences:{offscreen:true}});await window.loadFile(path.join(__dirname,'dist/index.html'));
 const run=code=>window.webContents.executeJavaScript(code);
 for(let i=0;i<100&&!await run("Boolean(document.querySelector('[aria-label=更新安装进度]'))");i++)await delay(20);
 assert.equal(await run("document.querySelector('[aria-label=更新安装进度]').innerText.includes('取消准备')"),true);
 if(process.env.COWORK_UPDATE_SCREENSHOTS){await delay(200);await fs.mkdir(process.env.COWORK_UPDATE_SCREENSHOTS,{recursive:true});await fs.writeFile(path.join(process.env.COWORK_UPDATE_SCREENSHOTS,'update-preparing.png'),(await window.webContents.capturePage()).toPNG());}
 await run("document.querySelector('[aria-label=更新安装进度] button').click()");await delay(50);
 assert.deepEqual(await run('window.calls'),['updates.cancel']);
 assert.equal(await run("document.querySelector('[aria-label=更新安装进度]').innerText.includes('正在取消')"),true);
 await run("window.setUpdate({phase:'ready',busy:false,ready:true,can_install:true,notice:true,install:{id:'a'.repeat(32),phase:'cancelled',error:''},message:'已取消本次安装，当前版本继续可用。'})");await delay(50);
 assert.equal(await run("Boolean(document.querySelector('[aria-label=可安装的更新]'))"),true);
 if(process.env.COWORK_UPDATE_SCREENSHOTS){await run("window.setUpdate({message:'更新已准备好。'})");await delay(200);await fs.writeFile(path.join(process.env.COWORK_UPDATE_SCREENSHOTS,'update-ready.png'),(await window.webContents.capturePage()).toPNG());}
 window.setSize(850,620);window.webContents.setZoomFactor(1.5);await run("document.documentElement.dataset.theme='dark';window.setUpdate({last_install_result:{phase:'rolled_back',error:'更新未完成，已恢复原版本。安装文件校验失败，可重新准备安装包。'}})");await delay(200);
 assert.equal(await run('document.documentElement.scrollWidth<=innerWidth'),true);
 assert.equal(await run("document.body.innerText.includes('已恢复原版本')"),true);
 if(process.env.COWORK_UPDATE_SCREENSHOTS)await fs.writeFile(path.join(process.env.COWORK_UPDATE_SCREENSHOTS,'update-recovered-dark.png'),(await window.webContents.capturePage()).toPNG());
 console.log('UPDATE_UI_OK');
}).then(()=>{clearTimeout(deadline);window?.destroy();app.exit(0);}).catch(error=>{console.error(error);clearTimeout(deadline);window?.destroy();app.exit(1);});
`);
    const env={...process.env,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const syntax=spawnSync(process.execPath,['--check',path.join(data,'fixture.cjs')],{encoding:'utf8',windowsHide:true});assert.equal(syntax.status,0,syntax.stderr);
    const child=spawn(require('electron'),[path.join(data,'fixture.cjs')],{env,windowsHide:true,timeout:30000,stdio:['ignore','pipe','pipe']});let output='';
    child.stdout.on('data',v=>output+=v);child.stderr.on('data',v=>output+=v);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/UPDATE_UI_OK/);
  }finally{await fs.rm(data,{recursive:true,force:true,maxRetries:3,retryDelay:100});}
});
