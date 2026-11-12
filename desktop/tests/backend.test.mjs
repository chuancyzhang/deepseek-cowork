import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Backend } from '../electron/backend.cjs';
import net from 'node:net';

test('a service reset during authentication rejects without an unhandled readline error', async () => {
  const server = net.createServer(socket => socket.resetAndDestroy());
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const backend = Object.create(Backend.prototype);
  backend.pending = new Map(); backend.detached = true;
  try {
    await assert.rejects(backend.connect({ port: server.address().port, token: 'fixture', instance: 'fixture' }), error => error.code === 'ECONNRESET');
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('protocol upgrade waits for saved shutdown before launching the new service', async()=>{
  const messages=[],sockets=new Set();let descriptor,launched=false;
  const server=net.createServer(socket=>{
    sockets.add(socket);socket.on('close',()=>sockets.delete(socket));
    let buffer='';socket.on('data',chunk=>{buffer+=chunk;let index;while((index=buffer.indexOf('\n'))>=0){
      const value=JSON.parse(buffer.slice(0,index));buffer=buffer.slice(index+1);
      if(value.token)socket.write(JSON.stringify({event:'ready',protocol:1,instance:'old'})+'\n');
      else {assert.equal(value.method,'app.shutdown');messages.push('saved');descriptor=null;socket.write(JSON.stringify({id:value.id,result:{saved:true}})+'\n');}
    }});
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  descriptor={protocol:1,port:server.address().port,token:'fixture',instance:'old'};
  const backend=Object.create(Backend.prototype);Object.assign(backend,{pending:new Map(),publish:event=>messages.push(event.event),detached:false,descriptor:()=>descriptor});
  const connect=backend.connect.bind(backend);
  backend.connect=async value=>value.protocol===1?connect(value):{event:'ready',protocol:2,instance:'new'};
  backend.launch=()=>{assert.ok(messages.includes('saved'));launched=true;descriptor={protocol:2,instance:'new'};};
  try {const ready=await backend.start();assert.equal(ready.protocol,2);assert.equal(launched,true);}
  finally {backend.detach();for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve));}
});

test('startup progress extends the wait, while stalled initialization still fails visibly',async()=>{
  for(const advancing of [true,false]) {
    const sockets=new Set(),timers=[];
    const server=net.createServer(socket=>{
      sockets.add(socket);socket.on('close',()=>sockets.delete(socket));
      socket.once('data',()=>{
        const status={event:'starting',protocol:2,instance:'startup',message:'正在恢复历史'};
        socket.write(JSON.stringify(status)+'\n');
        if(advancing)for(let i=1;i<=6;i++)timers.push(setTimeout(()=>socket.write(JSON.stringify(
          i===6?{...status,event:'ready'}:{...status,message:'已恢复 '+i})+'\n'),i*25));
      });
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const events=[],backend=Object.create(Backend.prototype);
    Object.assign(backend,{pending:new Map(),detached:true,startupTimeoutMs:80,publish:event=>events.push(event)});
    try {
      const ready=backend.connect({port:server.address().port,token:'fixture',protocol:2,instance:'startup'});
      if(advancing){assert.equal((await ready).event,'ready');assert.ok(events.length>=5);}
      else await assert.rejects(ready,/启动进度长时间未更新/);
    } finally {timers.forEach(clearTimeout);backend.detach();for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve));}
  }
});

test('exit during initialization stops the owned service, releases its lease and allows reopening', {skip:!process.env.COWORK_PYTHON},async()=>{
  const root=fileURLToPath(new URL('../../',import.meta.url)),qa=path.join(root,'.tmp/desktop/host-tests');
  await fs.mkdir(qa,{recursive:true});const dataDir=await fs.mkdtemp(path.join(qa,'startup-exit-'));
  let started,second;
  const observed=new Promise(resolve=>{started=resolve;});
  const first=new Backend({root,dataDir,resources:process.env.COWORK_TEST_DESKTOP_RESOURCES,publish:event=>{if(event.event==='service_starting')started();}});
  const outcome=first.ready.then(()=>null,error=>error);
  try {
    await observed;const pid=first.pid;assert.ok(pid);
    await first.stop();assert.ok(await outcome);
    assert.throws(()=>process.kill(pid,0),error=>error.code==='ESRCH');
    assert.equal((await fs.readdir(dataDir)).includes('desktop-service.json'),false);
    second=new Backend({root,dataDir,resources:process.env.COWORK_TEST_DESKTOP_RESOURCES,publish:()=>{}});await second.ready;
    assert.ok((await second.call('app.bootstrap')).settings);
  } finally {
    if(second)await second.stop();else if(!first.detached)await first.stop();
    await fs.rm(dataDir,{recursive:true,force:true,maxRetries:3,retryDelay:100});
  }
});

test('desktop hosts reconnect to the same independent service', { skip: !process.env.COWORK_PYTHON }, async () => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const qa = path.join(root, '.tmp/desktop/host-tests');
  await fs.mkdir(qa, { recursive: true });
  const dataDir = await fs.mkdtemp(path.join(qa, 'host-'));
  let first, second;
  try {
    first = new Backend({ root, dataDir, resources:process.env.COWORK_TEST_DESKTOP_RESOURCES, publish: () => {} });
    const initial = await first.ready;
    const { pid } = JSON.parse(await fs.readFile(path.join(dataDir, 'desktop-service.json'), 'utf8'));
    const conversation = await first.call('conversation.create');
    await first.call('draft.save', { conversation_id: conversation.id, revision: -1, value: { text: '重新打开后继续', attachments: [] } });
    first.detach();
    second = new Backend({ root, dataDir, resources:process.env.COWORK_TEST_DESKTOP_RESOURCES, publish: () => {} });
    assert.equal((await second.ready).instance, initial.instance);
    const restored = await second.call('conversation.get', { conversation_id: conversation.id });
    assert.equal(restored.draft.text, '重新打开后继续');
    await second.stop(); second = null;
    for (let i = 0; i < 100; i++) {
      try { process.kill(pid, 0); }
      catch { break; }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  } finally {
    if (second) await second.stop();
    else first?.detach();
    await fs.rm(dataDir, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
});

test('retry after a failed startup launches again without retaining the old exit error', {skip:!process.env.COWORK_PYTHON},async()=>{
  const root=fileURLToPath(new URL('../../',import.meta.url));
  const qa=path.join(root,'.tmp/desktop/host-tests');await fs.mkdir(qa,{recursive:true});
  const dataDir=await fs.mkdtemp(path.join(qa,'retry-'));let backend,started=false;
  try {
    await fs.writeFile(path.join(dataDir,'config.json'),'{invalid');
    backend=new Backend({root,dataDir,resources:process.env.COWORK_TEST_DESKTOP_RESOURCES,publish:()=>{}});
    await assert.rejects(backend.ready,/后台启动(?:失败|已停止)/);
    await fs.writeFile(path.join(dataDir,'config.json'),'{}');
    backend.ready=backend.start();await backend.ready;started=true;
    assert.ok((await backend.call('app.bootstrap')).settings);
  } finally {
    if(started)await backend.stop();else backend?.detach();
    for(let i=0;i<100;i++){try{await fs.access(path.join(dataDir,'desktop-service.json'));}catch{break;}await new Promise(resolve=>setTimeout(resolve,100));}
    await fs.rm(dataDir,{recursive:true,force:true,maxRetries:3,retryDelay:100});
  }
});
