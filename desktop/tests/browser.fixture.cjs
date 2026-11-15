const {app, BrowserWindow, nativeImage} = require('electron');
const {BrowserHost} = require('../electron/browser.cjs');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs/promises');

app.setPath('userData', path.join(process.env.COWORK_BROWSER_TEST_DATA, 'profile'));
app.commandLine.appendSwitch('force-device-scale-factor', process.env.COWORK_BROWSER_TEST_SCALE);
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async predicate => {
  for (let i = 0; i < 200; i++) { if (await predicate()) return; await wait(20); }
  throw Error('browser state did not settle');
};
let parent, host, server;
const deadline = setTimeout(() => { console.error('browser fixture timeout'); app.exit(1); }, 24000);
app.whenReady().then(async () => {
  const submissions = [], diagnostics = [], results = new Map();
  let sequence = 0, rejected = false;
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/submit') submissions.push(url.searchParams.get('q'));
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end(url.pathname === '/submit' ? '<title>Submitted</title><h1>Search result</h1>' : `<!doctype html><title>Form</title>
      <style>body{margin:0;background:#20a050}form{position:absolute;top:32px;right:24px}input{width:120px}button{width:100px;height:40px}</style>
      <form action="/submit"><input aria-label="Query" name="q"><button>Search</button></form>
      <script>window.trustedClicks=0;document.querySelector('button').addEventListener('click',e=>{if(e.isTrusted)trustedClicks++})</script>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  parent = new BrowserWindow({show:false, width:1200, height:900, webPreferences:{sandbox:true}});
  await parent.loadURL('data:text/html,<body style="background:white">Workspace');
  const backend = {call:async (method, args) => {
    if (method === 'browser.host_check' && rejected) throw Error('control_changed');
    if (method === 'browser.host_result') results.set(args.id, args.result);
    if (method === 'workbench.list') return {tabs:[...host.views.values()].map(item => ({id:item.id, owner:item.owner}))};
    return {ok:true};
  }};
  host = new BrowserHost({root:path.resolve(__dirname, '../..'), backend, getWindow:() => parent,
    diagnostic:(event, details) => diagnostics.push({event, ...details})});
  const item = host.create('first', {id:'first-tab', owner:'agent', url}), wc = item.view.webContents;
  const other = host.create('second', {id:'second-tab', owner:'agent', url});
  await other.view.webContents.loadURL(url);
  const request = async (action, args = {}, target = item) => {
    const id = String(++sequence);
    await host.handle({id, action, conversation_id:target.cid, tab:{id:target.id, owner:target.owner}, arguments:args});
    return results.get(id);
  };
  const observe = async () => {
    const result = await request('observe');
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.equal(result.image_status, 'ready', result.screenshot_note);
    const image = nativeImage.createFromDataURL(result.screenshot.image_url.url);
    assert.deepEqual(image.getSize(), result.viewport);
    return {result, image};
  };
  // This starts with both the application and its browser panel hidden. The old
  // host failed capture here and also lost real clicks after layout changes.
  for (const zoom of [1, .8, 1.25]) {
    await wc.loadURL(url);
    wc.setZoomFactor(zoom);
    const {result:initial, image} = await observe();
    const pixel = image.toBitmap().subarray((20 * image.getSize().width + 20) * 4, (20 * image.getSize().width + 20) * 4 + 4);
    assert.deepEqual([...pixel], [80,160,32,255], 'capture must contain the page, not a blank surface');
    if (zoom === 1 && process.env.COWORK_BROWSER_SCREENSHOT) await fs.writeFile(process.env.COWORK_BROWSER_SCREENSHOT, image.toPNG());
    const text = `query-${zoom}`;
    const fill = await request('browser_action', {action:'fill', element_id:initial.elements.find(el => el.tag === 'input').id, page_version:initial.page_version, text});
    assert.equal(fill.status, 'verified', JSON.stringify(fill));
    const {result:filled} = await observe();
    // Use screenshot/CSS coordinates at zoom 80%, beyond the native DIP width.
    const coords = await wc.executeJavaScript(`(()=>{const r=document.querySelector('button').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    const click = await request('browser_action', {action:zoom === .8 ? 'click_at' : 'click', ...coords,
      element_id:filled.elements.find(el => el.tag === 'button').id, page_version:filled.page_version});
    assert.equal(click.status, 'dispatched', JSON.stringify(click));
    await until(() => wc.getURL().includes('/submit') && item.ready);
    assert.equal(await wc.executeJavaScript('document.querySelector("h1").textContent'), 'Search result');
    assert.equal(submissions.filter(value => value === text).length, 1, 'one action must submit once');
    const repeated = await request('browser_action', {action:'click', element_id:filled.elements.find(el => el.tag === 'button').id, page_version:filled.page_version});
    assert.equal(repeated.ok, false);
    assert.equal(other.view.webContents.getURL(), url, 'another conversation must not navigate');
  }
  await wc.loadURL(url); wc.setZoomFactor(1);
  parent.showInactive();
  const surface = {conversation_id:item.cid, tab_id:item.id, bounds:{x:120,y:100,width:900,height:650}};
  await host.show(surface);
  assert.equal(host.inputShield.getVisible(), true);
  const {result:shown} = await observe();
  assert.equal((await request('browser_action', {action:'fill', element_id:shown.elements.find(el => el.tag === 'input').id,
    page_version:shown.page_version, text:'visible-enter'})).status, 'verified');
  const {result:focused} = await observe();
  assert.equal((await request('browser_action', {action:'key', key:'Enter', page_version:focused.page_version})).status, 'dispatched');
  await until(() => wc.getURL().includes('/submit') && item.ready);
  assert.equal(submissions.filter(value => value === 'visible-enter').length, 1);
  await wc.loadURL(url);
  item.owner = 'human';
  await host.show(surface);
  assert.equal(host.inputShield.getVisible(), false);
  await assert.rejects(host.show({...surface, conversation_id:'second'}), /重新打开/);
  await host.show(surface);
  host.hide(); parent.minimize();
  await observe();
  const background = host.backgroundWindows.get(item.view);
  assert.equal(background.getOpacity(), 0);
  assert.equal(background.isFocusable(), false);
  assert.equal(background.isFocused(), false);
  assert.equal(background.contentView.children.includes(item.view), true);
  assert.equal(parent.contentView.children.includes(item.view), false);
  const concurrent = await Promise.all([request('observe'), request('observe', {}, other)]);
  assert.deepEqual(concurrent.map(value => [value.tab_id, value.image_status]), [['first-tab','ready'], ['second-tab','ready']]);
  const capture = host.capture;
  host.capture = async () => { throw Error('test capture failure'); };
  const failedImage = await request('observe');
  assert.equal(failedImage.ok, true);
  assert.equal(failedImage.image_status, 'failed');
  assert.equal(failedImage.screenshot, undefined);
  assert(failedImage.elements.some(el => el.tag === 'button'));
  host.capture = capture;
  rejected = true;
  assert.equal((await request('observe')).ok, false);
  rejected = false;
  assert(diagnostics.some(value => value.event === 'browser_request_finished' && value.image_status === 'ready'));
  assert(diagnostics.some(value => value.event === 'browser_request_finished' && value.error === 'control_changed'));
  assert(diagnostics.some(value => value.event === 'browser_capture_failed' && value.error === 'test capture failure'));
  await request('close');
  await request('close', {}, other);
  assert.equal(host.views.size, 0);
  assert.equal(host.backgroundWindows.size, 0);
  console.log('BROWSER_FIXTURE_OK');
}).then(() => finish(0)).catch(error => {console.error(error); finish(1);});
function finish(code) {
  clearTimeout(deadline);
  for (const item of host?.views.values() || []) item.view.webContents?.close();
  host?.inputShield?.webContents?.close();
  for (const background of host?.backgroundWindows.values() || []) background.destroy();
  parent?.destroy();
  server?.close();
  app.exit(code);
}
