import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {prepareInstall,commitInstall,UpdateStartup} from '../electron/updates.cjs';

const id='a'.repeat(32);
test('slow preparation is polled; cancellation uses the identity known before the first response',async()=>{
  const calls=[];let polls=0;
  const backend={call:async(method,p)=>{
    calls.push([method,p]);
    if(method==='updates.prepare_install')return {id,phase:'preparing'};
    if(method==='updates.install_status')return {id,phase:++polls<300?'preparing':'ready'};
  }};
  assert.deepEqual(await prepareInstall(backend,12,{id,sleep:async()=>{}}),{id});
  assert.equal(polls,300); // More than 60 seconds of normal 250 ms polls.
  calls.length=0;
  backend.call=async(method,p)=>{calls.push([method,p]);if(method==='updates.prepare_install')throw Error('response lost');return {phase:'cancelled'};};
  await assert.rejects(prepareInstall(backend,12,{id}),/response lost/);
  assert.deepEqual(calls.map(c=>c[0]),['updates.prepare_install','updates.abort_install']);
  assert.equal(calls[1][1].id,id);
});

test('lost commit response checks durable commit and does not abort completed work',async()=>{
  const qa=path.resolve('../.tmp/update-lifecycle-fix/node');await fs.mkdir(qa,{recursive:true});
  const root=await fs.mkdtemp(path.join(qa,'commit-'));
  const transaction=path.join(root,'updates/desktop/transactions',id);
  try{
    await fs.mkdir(transaction,{recursive:true});await fs.writeFile(path.join(transaction,'commit.json'),JSON.stringify({id}));
    let stopped=false;
    const backend={call:async method=>{assert.equal(method,'updates.commit_install');throw Error('connection closed');}};
    await commitInstall(backend,{id},root,async options=>{assert.equal(options.serviceStopped,true);stopped=true;});
    assert.equal(stopped,true);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});

test('a completed user cancellation returns quietly without attempting commit',async()=>{
  const calls=[];
  const backend={call:async method=>{calls.push(method);return {phase:'cancelled'};}};
  assert.deepEqual(await prepareInstall(backend,12,{id}),{cancelled:true});
  assert.deepEqual(calls,['updates.prepare_install']);
});

test('uncertain commit is never converted into an abort or another commit',async()=>{
  const calls=[];
  const backend={call:async method=>{calls.push(method);if(method==='updates.commit_install')throw Error('timeout');return {phase:'committing'};}};
  await assert.rejects(commitInstall(backend,{id},path.resolve('../.tmp/update-lifecycle-fix/absent'),()=>assert.fail('must not quit')),/提交阶段/);
  assert.deepEqual(calls,['updates.commit_install','updates.install_status']);
});

test('duplicate update launch is queued until the actual window is ready and checked by the service',async()=>{
  const install=path.resolve('../.tmp/update-lifecycle-fix/install'),calls=[];
  const startup=new UpdateStartup({installDir:install,version:'5.4.0',call:async(method,p)=>{calls.push([method,p]);return {acknowledged:true};}});
  const request={id,receipt:path.join(install,'opened.json'),install_dir:install};
  startup.accept(request);startup.accept(request);
  assert.deepEqual(await startup.flush(),{acknowledged:false});assert.equal(calls.length,0);
  startup.ready=true;
  assert.deepEqual(await startup.flush(),{acknowledged:true});assert.equal(calls.length,1);
  assert.deepEqual(calls[0][1],{request,host_version:'5.4.0'});
  startup.accept({...request,id:'b'.repeat(32)});await startup.flush();assert.equal(calls.length,2);
  startup.accept({...request,install_dir:path.join(install,'other')});await startup.flush();assert.equal(calls.length,2);
});

test('a failed startup acknowledgement remains pending for explicit reconnection',async()=>{
  const install=path.resolve('../.tmp/update-lifecycle-fix/install');let succeeds=false;
  const startup=new UpdateStartup({installDir:install,version:'5.4.0',call:async()=>{if(!succeeds)throw Error('offline');return {acknowledged:true};}});
  startup.accept({id,receipt:'test',install_dir:install});startup.ready=true;
  await assert.rejects(startup.flush(),/offline/);assert.equal(startup.pending.size,1);
  succeeds=true;await startup.flush();assert.equal(startup.pending.size,0);
});

test('real desktop duplicate launch forwards an update receipt to the existing window',
  {skip:!process.env.COWORK_PYTHON,timeout:60000},async()=>{
  const require=createRequire(import.meta.url),root=fileURLToPath(new URL('../../',import.meta.url));
  const version=(await fs.readFile(path.join(root,'core/app_version.py'),'utf8')).match(/APP_VERSION\s*=\s*["']([^"']+)/)?.[1];
  assert.ok(version);
  const qa=path.join(root,'.tmp/update-lifecycle-fix/native');await fs.mkdir(qa,{recursive:true});
  const data=await fs.mkdtemp(path.join(qa,'duplicate-')),children=[];let socket,first;
  const exe=process.env.COWORK_TEST_DESKTOP_EXE||require('electron');
  const installDir=process.env.COWORK_TEST_DESKTOP_EXE?path.dirname(exe):root;
  const env={...process.env,COWORK_APP_DATA_DIR:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const timeout=async(promise,ms)=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('native probe timeout')),ms);})]);}finally{clearTimeout(timer);}};
  const launch=extra=>{const p=spawn(exe,[...(process.env.COWORK_TEST_DESKTOP_EXE?[]:[path.join(root,'desktop')]),'--remote-debugging-port=0'],{env:{...env,...extra},windowsHide:true,stdio:'ignore'});
    p.done=new Promise((resolve,reject)=>{p.on('exit',resolve);p.on('error',reject);});children.push(p);return p;};
  const until=async(fn)=>{for(let i=0;i<300;i++){try{const value=await fn();if(value)return value;}catch{}await delay(100);}throw Error('native condition timeout');};
  try{
    first=launch({});
    const port=await until(async()=>Number((await fs.readFile(path.join(data,'desktop-browser/DevToolsActivePort'),'utf8')).split('\n')[0]));
    const targets=async()=>(await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).filter(t=>t.type==='page'&&t.url.startsWith('file:'));
    const target=await until(async()=>(await targets())[0]);
    socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
    let sequence=0;const pending=new Map();
    socket.onmessage=({data:message})=>{const value=JSON.parse(message);if(pending.has(value.id)){pending.get(value.id)(value);pending.delete(value.id);}};
    const evaluate=expression=>timeout(new Promise(resolve=>{const identity=++sequence;pending.set(identity,resolve);socket.send(JSON.stringify({id:identity,method:'Runtime.evaluate',params:{expression,awaitPromise:true,returnByValue:true}}));}),20000);
    const ready=await evaluate("window.cowork.call('app.bootstrap').then(()=>window.cowork.call('app.ready')).then(()=>true)");
    assert.equal(ready.result?.result?.value,true,JSON.stringify(ready));
    const original=JSON.parse(await fs.readFile(path.join(data,'desktop-service.json'),'utf8'));
    for(let i=0;i<3;i++)assert.equal(await timeout(launch({}).done,8000),0);
    const transaction=path.join(data,'updates/desktop/transactions',id);await fs.mkdir(transaction,{recursive:true});
    await fs.writeFile(path.join(transaction,'config.json'),JSON.stringify({id,install_dir:installDir,version}));
    await fs.writeFile(path.join(transaction,'commit.json'),JSON.stringify({id}));
    await fs.writeFile(path.join(transaction,'transaction.json'),JSON.stringify({id,phase:'installed'}));
    const receipt=path.join(transaction,'opened.json');
    assert.equal(await timeout(launch({COWORK_UPDATE_ID:id,COWORK_UPDATE_RECEIPT:receipt}).done,8000),0);
    const acknowledged=await until(async()=>JSON.parse(await fs.readFile(receipt,'utf8')));
    assert.equal(acknowledged.id,id);assert.equal(acknowledged.version,version);
    assert.equal((await targets()).length,1);assert.equal(first.exitCode,null);
    const after=JSON.parse(await fs.readFile(path.join(data,'desktop-service.json'),'utf8'));
    assert.equal(after.pid,original.pid);
    void evaluate("window.cowork.call('app.quit')").catch(()=>{});
    assert.equal(await timeout(first.done,15000),0);
  }finally{
    socket?.close();for(const child of children)if(child.exitCode===null){child.kill();await child.done;}
    await fs.rm(data,{recursive:true,force:true,maxRetries:3,retryDelay:100});
  }
});
