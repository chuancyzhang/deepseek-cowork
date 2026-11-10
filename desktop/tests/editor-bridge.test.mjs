import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import fs from 'node:fs';

test('built-in editor bridge only accepts parent commands and preserves chunk order', async () => {
  const events = [], calls = [];
  let listener, bridge;
  const parent = { postMessage: event => events.push(event) };
  const window = { addEventListener: (_event, callback) => { listener = callback; }, coworkEditor: {
    beginLoad: (...args) => calls.push(['begin', ...args]), appendLoadChunk: (...args) => calls.push(['chunk', ...args]), finishLoad: () => calls.push(['finish']),
  } };
  vm.runInNewContext(fs.readFileSync('../web/editors/static/desktop-bridge.js', 'utf8'), { window, parent });
  window.connectDesktopBridge('deliverableEditorBridge', value => { bridge = value; });
  bridge.ready('sheet');
  assert.equal(events[0].method, 'ready');
  const send = (method, args, source = parent) => listener({ source, data: { channel: 'cowork-editor-host-v1', method, args } });
  await send('beginLoad', ['session', 2]);
  await send('appendLoadChunk', [0, 'first']);
  await send('appendLoadChunk', [1, 'second']);
  await send('finishLoad', []);
  await send('beginLoad', ['foreign', 1], {});
  assert.deepEqual(calls, [['begin', 'session', 2], ['chunk', 0, 'first'], ['chunk', 1, 'second'], ['finish']]);
  await send('constructor', []);
  assert.equal(events.at(-1).method, 'reportError');
});

test('built editor pages use the native bridge without Qt scripts', () => {
  for (const name of ['docx', 'docx-review', 'sheet', 'html', 'terminal']) {
    const html = fs.readFileSync(`../web/editors/dist/${name}.html`, 'utf8');
    assert.match(html, /desktop-bridge\.js/);
    assert.doesNotMatch(html, /qrc:|qwebchannel/i);
  }
});
