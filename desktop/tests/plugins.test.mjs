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
