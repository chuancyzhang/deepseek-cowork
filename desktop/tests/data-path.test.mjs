import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveDataDirectory } from '../electron/data-path.cjs';

test('production reuses established data, development stays isolated, portable uses app root', async () => {
  const qa = fileURLToPath(new URL('../../.tmp/desktop/data-path/', import.meta.url));
  await fs.mkdir(qa, { recursive: true });
  const root = await fs.mkdtemp(path.join(qa, 'run-'));
  try {
    const args = { packaged: true, executable: path.join(root, 'Cowork.exe'), appData: root };
    assert.equal(resolveDataDirectory(args), path.join(root, 'DeepSeekCowork'));
    assert.equal(resolveDataDirectory({ ...args, packaged: false }), path.join(root, 'DeepSeekCoworkNext'));
    await fs.mkdir(path.join(root, 'user_data'));
    assert.equal(resolveDataDirectory(args), path.join(root, 'user_data'));
    assert.equal(resolveDataDirectory({ ...args, explicit: path.join(root, 'custom') }), path.join(root, 'custom'));
    assert.throws(() => resolveDataDirectory({ ...args, explicit: 'relative-data' }), /绝对路径/);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
