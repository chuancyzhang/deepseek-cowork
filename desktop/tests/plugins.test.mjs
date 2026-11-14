import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { PluginHost } from '../electron/plugins.cjs';

test('preview snapshot is immutable, installation and rollback preserve versions', async () => {
  const qa = path.resolve('../.tmp/desktop/plugin-tests');
  await fs.mkdir(qa, {recursive:true});
  const root = await fs.mkdtemp(path.join(qa, 'run-'));
  try {
    const host = new PluginHost(path.join(root,'data'), path.resolve('plugins/writing'));
    const base = await host.load('cowork.writing');
    const exported = await host.export('cowork.writing', root);
    const manifestPath = path.join(exported.path, 'manifest.json');
    const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
    manifest.version = '1.0.1';
    await fs.writeFile(manifestPath, JSON.stringify(manifest));
    const preview = await host.preview(exported.path);
    await fs.writeFile(path.join(exported.path, 'index.html'), 'changed after preview');
    await host.install(preview.token);
    assert.equal((await host.load('cowork.writing')).hash, preview.hash);
    await host.rollback('cowork.writing');
    assert.equal((await host.load('cowork.writing')).hash, base.hash);
    await assert.rejects(() => host.load('../escape'));
  } finally { await fs.rm(root, {recursive:true, force:true}); }
});

test('v2 packages hash local assets, compose panels, reject command collisions and restore builtins',async()=>{
  const qa=path.resolve('../.tmp/desktop/plugin-tests');await fs.mkdir(qa,{recursive:true});const root=await fs.mkdtemp(path.join(qa,'v2-'));
  try{
    const host=new PluginHost(path.join(root,'data'),path.resolve('plugins/writing'));
    const source=path.join(root,'panel');await fs.cp(path.resolve('examples/panel'),source,{recursive:true});
    const preview=await host.preview(source);await host.install(preview.token);
    const initial=await host.inventory();
    assert.deepEqual(initial.selected['panel:tasks'],['cowork.workbench/tasks','example.task-notes/notes']);
    assert.ok(host.asset(preview.hash,'client.js').body.length>0);
    await fs.appendFile(path.join(source,'style.css'),'\nbody{margin:1px}');
    const revised=await host.preview(source);assert.notEqual(revised.hash,preview.hash);await host.install(revised.token);
    assert.equal((await host.load('example.task-notes')).hash,revised.hash);
    const back=await host.rollbackPreview('example.task-notes');assert.equal(back.hash,preview.hash);await host.install(back.token);
    await host.disable('example.task-notes');
    assert.equal((await host.inventory()).catalog.find(item=>item.id==='example.task-notes').enabled,false);
    const enabled=await host.enablePreview('example.task-notes');await host.install(enabled.token);
    assert.ok((await host.inventory()).selected['panel:tasks'].includes('example.task-notes/notes'));
    const manifest=JSON.parse(await fs.readFile(path.join(source,'manifest.json'),'utf8'));manifest.id='example.other';
    await fs.writeFile(path.join(source,'manifest.json'),JSON.stringify(manifest));
    const conflict=await host.preview(source);await assert.rejects(host.install(conflict.token),/命令标识冲突/);
    assert.equal((await host.inventory()).plugins.filter(item=>item.enabled&&item.id==='example.other').length,0);
    const recovered=await host.recover();assert.deepEqual(recovered.selected['editor:text'],['cowork.writing/writing']);
    assert.deepEqual(recovered.selected['workbench:main'],['cowork.workbench/main']);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});

test('external examples use the published SDK client',async()=>{
  const client=await fs.readFile('sdk/client.js','utf8');
  for(const example of ['workbench','panel'])assert.equal(await fs.readFile(`examples/${example}/client.js`,'utf8'),client);
});
