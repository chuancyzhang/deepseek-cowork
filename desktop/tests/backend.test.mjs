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

test('desktop hosts reconnect to the same independent service', { skip: !process.env.COWORK_PYTHON }, async () => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const qa = path.join(root, '.tmp/desktop/host-tests');
  await fs.mkdir(qa, { recursive: true });
  const dataDir = await fs.mkdtemp(path.join(qa, 'host-'));
  let first, second;
  try {
    first = new Backend({ root, dataDir, publish: () => {} });
    const initial = await first.ready;
    const { pid } = JSON.parse(await fs.readFile(path.join(dataDir, 'desktop-service.json'), 'utf8'));
    const conversation = await first.call('conversation.create');
    await first.call('draft.save', { conversation_id: conversation.id, revision: -1, value: { text: '重新打开后继续', attachments: [] } });
    first.detach();
    second = new Backend({ root, dataDir, publish: () => {} });
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
