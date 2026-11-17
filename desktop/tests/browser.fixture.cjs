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
const deadline = setTimeout(() => { console.error('browser fixture timeout'); app.exit(1); }, 50000);
app.whenReady().then(async () => {
  const submissions = [], diagnostics = [], downloadRequests = [], results = new Map();
  let sequence = 0, rejected = false, rejectAfterReceipt = false, rejectAfterDispatch = false;
  const receipts=new Map(),unresolved=new Map(),requestTabs=new Map();
  const inspection=new Set(['scroll','scroll_into_view','hover']);
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/submit') submissions.push(url.searchParams.get('q'));
    if(url.pathname==='/download'){res.setHeader('Content-Disposition','attachment; filename="result.txt"');res.end('download result');return;}
    if(url.pathname==='/no-download'){downloadRequests.push(url.pathname);res.writeHead(204);res.end();return;}
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    if(url.pathname==='/frame'){res.end('<button onclick="this.textContent=\'Frame done\'">Frame action</button>');return;}
    if(url.pathname==='/advanced'){
      res.end(`<!doctype html><title>Advanced</title><style>body{margin:8px}button{min-width:70px;height:30px}iframe{width:300px;height:100px}#far{display:block;margin-top:1100px}</style>
      <p id="clock">Time</p><p id="price">Price 10</p><input aria-label="Amount" oninput="this.value=this.value.toUpperCase()"><input aria-label="Name">
      <select aria-label="City"><option value="a">A</option><option value="b">B</option></select><label><input type="checkbox">Agree</label>
      <button id="count" onclick="window.totalClicks=(window.totalClicks||0)+1;document.querySelector('#result').textContent='Count '+window.totalClicks">Count</button><p id="result">Count 0</p>
      <section aria-label="First"><button>Duplicate</button></section><section aria-label="Second"><button>Duplicate</button></section>
      <input type="file" id="upload" aria-label="Upload" multiple style="display:none"><a download href="/download">Download</a><a href="/no-download">Pending download</a>
      <button onclick="window.open('/frame')">Popup</button><button onclick="document.querySelector('#result').textContent=confirm('Continue?')?'Accepted':'Declined'">Dialog</button>
      <div id="shadow"></div><iframe src="/frame"></iframe><iframe src="http://localhost:${server.address().port}/frame"></iframe>
      <script>const shadow=document.querySelector('#shadow').attachShadow({mode:'open'});shadow.innerHTML='<button>Shadow action</button>';shadow.querySelector('button').onclick=function(){this.textContent='Shadow done'}</script>
      <button id="far">Far</button>`);return;
    }
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
    if(method==='browser.host_dispatch'){
      const tab_id=requestTabs.get(args.id);
      if(unresolved.has(tab_id)&&!inspection.has(args.action))throw Error('unknown action must be reconciled');
      const receipt_id=args.id+':'+args.step;receipts.set(args.id,{receipt_id,tab_id,action:args.action,effect_state:'pending'});
      if(!inspection.has(args.action))unresolved.set(tab_id,receipt_id);
      if(rejectAfterDispatch)rejected=true;return {ok:true,receipt_id};
    }
    if(method==='browser.host_receipt'){const current=receipts.get(args.id);assert(current);receipts.set(args.id,{...current,...args.result});
      if(!inspection.has(current.action)&&['unknown','pending'].includes(args.result.effect_state))unresolved.set(current.tab_id,current.receipt_id);
      else if(unresolved.get(current.tab_id)===current.receipt_id)unresolved.delete(current.tab_id);
      if(rejectAfterReceipt)rejected=true;}
    if(method==='browser.host_popup')return {tab:{id:'popup-'+(++sequence),owner:'agent',url:args.url}};
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
    requestTabs.set(id,target.id);
    await host.handle({id, action, conversation_id:target.cid, tab:{id:target.id, owner:target.owner}, arguments:args});
    return results.get(id);
  };
  const observe = async () => {
    const result = await request('observe', {screenshot:'viewport'});
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
      element_id:filled.elements.find(el => el.tag === 'button').id, page_version:filled.page_version, screenshot_id:filled.screenshot_id});
    assert.equal(click.status, 'dispatched', JSON.stringify(click));
    await until(() => wc.getURL().includes('/submit') && item.ready);
    assert.equal(await wc.executeJavaScript('document.querySelector("h1").textContent'), 'Search result');
    assert.equal(submissions.filter(value => value === text).length, 1, 'one action must submit once');
    const repeated = await request('browser_action', {action:'click', element_id:filled.elements.find(el => el.tag === 'button').id, page_version:filled.page_version, screenshot_id:filled.screenshot_id});
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
  const concurrent = await Promise.all([request('observe', {screenshot:'viewport'}), request('observe', {screenshot:'viewport'}, other)]);
  assert.deepEqual(concurrent.map(value => [value.tab_id, value.image_status]), [['first-tab','ready'], ['second-tab','ready']]);
  const capture = host.capture;
  host.capture = async () => { throw Error('test capture failure'); };
  const failedImage = await request('observe', {screenshot:'viewport'});
  assert.equal(failedImage.ok, true);
  assert.equal(failedImage.image_status, 'failed');
  assert.equal(failedImage.screenshot, undefined);
  assert(failedImage.elements.some(el => el.tag === 'button'));
  host.capture = capture;
  // Extended workflows verify DOM effects and actual file results, not only receipts.
  item.owner='agent';await wc.loadURL(url+'advanced');
  const semantic=await request('observe');assert.equal(semantic.image_status,'not_requested');assert.equal(semantic.screenshot,undefined);
  const countRef=semantic.elements.find(el=>el.name==='Count').id;
  await wc.executeJavaScript("document.querySelector('#clock').textContent='New time'");
  const refreshed=await request('observe');assert.equal(refreshed.elements.find(el=>el.name==='Count').id,countRef);
  let acted=await request('browser_action',{action:'click',element_id:countRef,page_version:semantic.page_version});assert.equal(acted.status,'dispatched',JSON.stringify(acted));
  assert.equal(await wc.executeJavaScript('window.totalClicks'),1);
  let snapshot=acted.observation;
  acted=await request('browser_action',{action:'click',target:{role:'button',name:'Duplicate'},page_version:snapshot.page_version});assert.equal(acted.status,'ambiguous_target');
  acted=await request('browser_action',{action:'click',element_id:countRef,page_version:snapshot.page_version,expect:[{target:{selector:'#price'},field:'text',equals:'Price 11'}]});assert.equal(acted.status,'expectation_changed');
  assert.equal(await wc.executeJavaScript('window.totalClicks'),1);
  await wc.executeJavaScript("document.querySelector('#count').outerHTML=document.querySelector('#count').outerHTML");
  acted=await request('browser_action',{action:'click',element_id:countRef,page_version:snapshot.page_version});assert.equal(acted.status,'element_unavailable');
  acted=await request('browser_action',{action:'hover',target:{role:'button',name:'Count'},page_version:snapshot.page_version});
  assert.equal(acted.status,'dispatched',JSON.stringify(acted));
  assert.equal(await wc.executeJavaScript('window.totalClicks'),1,'semantic redraw must not replay the stale ref click');
  snapshot=await request('observe');
  acted=await request('browser_action',{action:'fill',target:{label:'Amount'},text:'abc',page_version:snapshot.page_version});
  assert.equal(acted.status,'dispatched',JSON.stringify(acted));assert.equal(acted.actual_value,'ABC');assert.equal(await wc.executeJavaScript("document.querySelector('input').value"),'ABC');
  snapshot=acted.observation;
  acted=await request('browser_action',{action:'batch',page_version:snapshot.page_version,steps:[
    {action:'fill',target:{label:'Name'},text:'Ada'},{action:'select',target:{label:'City'},values:['b']},
    {action:'check',target:{label:'Agree'},checked:true},{action:'click',target:{role:'button',name:'Missing'}},
    {action:'fill',target:{label:'Name'},text:'Must not execute'}]});
  assert.equal(acted.completed,3,JSON.stringify(acted));assert.equal(acted.not_executed,1);
  assert.deepEqual(await wc.executeJavaScript("[document.querySelector('input[aria-label=Name]').value,document.querySelector('select').value,document.querySelector('input[type=checkbox]').checked]"),['Ada','b',true]);
  snapshot=await request('observe');
  await wc.executeJavaScript("document.querySelector('[aria-label=Name]').oninput=function(){this.replaceWith(this.cloneNode(true))};true");
  acted=await request('browser_action',{action:'fill',target:{label:'Name'},text:'Redrawn',page_version:snapshot.page_version});
  assert.equal(acted.status,'dispatched',JSON.stringify(acted));assert.equal(unresolved.size,0);
  assert.equal(await wc.executeJavaScript("document.querySelector('[aria-label=Name]').value"),'Redrawn');
  const fields=await request('browser_read',{mode:'form'});assert.equal(fields.rows.find(row=>row.name==='Name').value,'Redrawn');
  snapshot=await request('observe');
  acted=await request('browser_action',{action:'click',target:{role:'button',name:'Shadow action'},page_version:snapshot.page_version});assert.equal(acted.ok,true,JSON.stringify(acted));
  assert.equal(await wc.executeJavaScript("document.querySelector('#shadow').shadowRoot.querySelector('button').textContent"),'Shadow done');
  snapshot=await request('observe');
  const frames=snapshot.frames.filter(frame=>frame.parent_id);assert.equal(frames.length,2,JSON.stringify(snapshot.frames));
  for(const frame of frames){const view=await request('observe',{frame_id:frame.id});assert.equal(view.ok,true,JSON.stringify(view));
    acted=await request('browser_action',{action:'click',target:{role:'button',name:'Frame action'},page_version:view.page_version,frame_id:frame.id});assert.equal(acted.ok,true,JSON.stringify(acted));
    const read=await request('browser_read',{frame_id:frame.id,target:{role:'button',name:'Frame done'}});assert.equal(read.rows.length,1,JSON.stringify(read));}
  const upload=path.join(process.env.COWORK_BROWSER_TEST_DATA,'upload.txt');await fs.writeFile(upload,'upload source');
  snapshot=await request('observe');acted=await request('browser_file',{operation:'upload',page_version:snapshot.page_version,target:{label:'Upload'},files:[upload]});assert.equal(acted.ok,true,JSON.stringify(acted));
  assert.equal(await wc.executeJavaScript("document.querySelector('#upload').files[0].name"),'upload.txt');
  snapshot=await request('observe');const downloaded=path.join(process.env.COWORK_BROWSER_TEST_DATA,'download.txt');
  acted=await request('browser_file',{operation:'download',page_version:snapshot.page_version,target:{role:'link',name:'Download'},path:downloaded});assert.equal(acted.status,'verified',JSON.stringify(acted));
  assert.equal(await fs.readFile(downloaded,'utf8'),'download result');
  snapshot=await request('observe');
  acted=await request('browser_file',{operation:'download',page_version:snapshot.page_version,target:{role:'link',name:'Pending download'},path:path.join(process.env.COWORK_BROWSER_TEST_DATA,'missing-download.txt'),timeout_ms:100});
  assert.equal(acted.status,'incomplete',JSON.stringify(acted));assert.equal(acted.effect_state,'dispatched');assert.equal(unresolved.size,0);
  assert.equal(downloadRequests.length,1,'download timeout must not replay its click');
  snapshot=await request('observe');assert.equal(snapshot.ok,true,JSON.stringify(snapshot));
  acted=await request('browser_action',{action:'click',target:{role:'button',name:'Popup'},page_version:snapshot.page_version});assert.equal(acted.ok,true,JSON.stringify(acted));
  await until(()=>[...host.views.values()].some(v=>v.id.startsWith('popup-')));const popup=[...host.views.values()].find(v=>v.id.startsWith('popup-'));assert.equal(popup.cid,item.cid);
  await until(()=>popup.ready);assert.equal(await popup.view.webContents.executeJavaScript('!!window.opener'),true);
  await popup.view.webContents.executeJavaScript("window.opener.document.querySelector('#result').textContent='Popup returned'");
  assert.equal(await wc.executeJavaScript("document.querySelector('#result').textContent"),'Popup returned');
  await popup.view.webContents.executeJavaScript('window.close()').catch(()=>{});await until(()=>!host.views.has(popup.id));
  snapshot=await request('observe');acted=await request('browser_action',{action:'click',target:{role:'button',name:'Dialog'},page_version:snapshot.page_version});
  assert(acted.dialog?.id,JSON.stringify(acted));assert.equal(acted.suspended,true);
  const dialogRead=await request('observe');assert.equal(dialogRead.dialog.message,'Continue?');
  const settled=await request('browser_action',{action:'dialog',dialog_id:acted.dialog.id,accept:true});assert.equal(settled.ok,true,JSON.stringify(settled));
  assert.equal(await wc.executeJavaScript("document.querySelector('#result').textContent"),'Accepted');
  snapshot=await request('observe');acted=await request('browser_action',{action:'click',target:{role:'button',name:'Count'},page_version:snapshot.page_version,wait:{state:'visible',target:{selector:'#never'}},wait_timeout_ms:100});
  assert.equal(acted.status,'incomplete',JSON.stringify(acted));assert.equal(acted.effect_state,'dispatched');assert.equal(acted.wait.status,'timeout');
  assert.equal(await wc.executeJavaScript('window.totalClicks'),2);assert.equal(unresolved.size,0);
  const continued=await request('browser_wait',{condition:{state:'text',target:{selector:'#result'},equals:'Count 2'}});assert.equal(continued.ok,true);
  snapshot=await request('observe');acted=await request('browser_action',{action:'fill',target:{label:'Name'},text:'Continue after wait',page_version:snapshot.page_version});
  assert.equal(acted.ok,true,JSON.stringify(acted));assert.equal(await wc.executeJavaScript('window.totalClicks'),2);
  const visual=await request('observe',{screenshot:'viewport'});acted=await request('browser_action',{action:'click_at',x:3,y:3,page_version:visual.page_version});assert.equal(acted.ok,false);
  const full=await request('observe',{screenshot:'full'});assert.equal(full.image_status,'ready',full.screenshot_note);assert.equal(full.screenshot_id,undefined);
  const textOnly=await request('observe',{screenshot:'none'});assert.equal(textOnly.image_status,'not_requested');assert.equal(textOnly.screenshot,undefined);
  const unchanged=await request('observe',{baseline:textOnly.page_version});assert.equal(unchanged.unchanged,true);assert.equal(unchanged.elements,undefined);assert(unchanged.page_version);
  await wc.executeJavaScript("{const p=document.createElement('p');p.id='long-read';p.textContent='Long result '.repeat(5000);document.body.append(p)}");
  const longRead=await request('browser_read',{target:{selector:'#long-read'},mode:'text'});assert(longRead.truncated);assert(longRead.rows[0].text.startsWith('Long result '));assert(JSON.stringify(longRead).length<24500);
  snapshot=await request('observe');acted=await request('browser_action',{action:'key',target:{label:'Name'},key:'Control+A',page_version:snapshot.page_version});assert(acted.ok,JSON.stringify(acted));
  acted=await request('browser_action',{action:'type',target:{label:'Name'},text:'Native text',page_version:acted.observation.page_version});assert(acted.ok,JSON.stringify(acted));
  assert.equal(await wc.executeJavaScript('document.querySelector("[aria-label=Name]").value'),'Native text');
  await wc.executeJavaScript("{const e=document.createElement('canvas');e.id='drawing';e.width=100;e.height=60;e.style.cssText='position:fixed;top:300px;left:10px';document.body.append(e);e.onpointerdown=()=>window.dragStart=true;e.onpointerup=()=>window.dragDone=!!window.dragStart;true}");
  const drawing=await request('observe',{screenshot:'viewport'});
  // Unrelated paint AND DOM attribute changes must not invalidate coordinates.
  await wc.executeJavaScript("document.querySelector('#clock').textContent='Another time';document.querySelector('#clock').className='updated'");
  acted=await request('browser_action',{action:'drag_at',x:30,y:320,to_x:90,to_y:340,screenshot_id:drawing.screenshot_id,page_version:drawing.page_version});assert(acted.ok,JSON.stringify(acted));assert.equal(await wc.executeJavaScript('window.dragDone'),true);
  const canvasShot=await request('observe',{screenshot:'viewport'});await wc.executeJavaScript("document.querySelector('#drawing').getContext('2d').fillRect(0,0,100,60)");
  acted=await request('browser_action',{action:'click_at',x:30,y:320,screenshot_id:canvasShot.screenshot_id,page_version:canvasShot.page_version});assert.equal(acted.status,'stale_page');
  snapshot=await request('observe');rejectAfterReceipt=true;
  acted=await request('browser_action',{action:'batch',page_version:snapshot.page_version,steps:[{action:'click',target:{role:'button',name:'Count'},wait:{state:'visible',target:{selector:'#never'}}},{action:'click',target:{role:'button',name:'Count'}}]});
  rejectAfterReceipt=false;rejected=false;assert.equal(acted.status,'incomplete');assert.equal(acted.not_executed,1);assert(acted.results[0].receipt_id);assert.equal(acted.results[0].effect_state,'dispatched');
  assert.equal(unresolved.size,0);assert.equal(await wc.executeJavaScript('window.totalClicks'),3);
  snapshot=await request('observe');rejectAfterDispatch=true;
  acted=await request('browser_action',{action:'click',target:{role:'button',name:'Count'},page_version:snapshot.page_version});
  rejectAfterDispatch=false;rejected=false;
  assert.equal(acted.effect_state,'not_dispatched');assert.equal(acted.executed,false);
  assert.equal(unresolved.size,0);assert.equal(await wc.executeJavaScript('window.totalClicks'),3);
  acted=await request('navigate',{url:wc.getURL()+'#section'});assert.equal(acted.effect_state,'dispatched');
  await until(()=>wc.getURL().endsWith('#section')&&item.ready);
  assert.equal((await request('observe')).ok,true,'same-document navigation must remain observable');
  // A genuinely lost dispatch response still blocks a repeat, while inspection
  // remains available. The durable reconciliation path has Python coverage.
  const native=host.controller.native;
  host.controller.native=async function(...args){await native.apply(this,args);throw Error('test lost dispatch response');};
  snapshot=await request('observe');acted=await request('browser_action',{action:'click',target:{role:'button',name:'Count'},page_version:snapshot.page_version});
  host.controller.native=native;
  assert.equal(acted.status,'unknown');assert.equal(await wc.executeJavaScript('window.totalClicks'),4);
  snapshot=await request('observe');acted=await request('browser_action',{action:'click',target:{role:'button',name:'Count'},page_version:snapshot.page_version});
  assert.equal(acted.ok,false);assert.equal(await wc.executeJavaScript('window.totalClicks'),4);
  snapshot=await request('observe');acted=await request('browser_action',{action:'scroll_into_view',target:{role:'button',name:'Far'},page_version:snapshot.page_version});assert.equal(acted.ok,true,JSON.stringify(acted));
  assert.equal(unresolved.size,1,'inspection must retain the original uncertain action');
  assert(await wc.executeJavaScript('scrollY')>100);
  rejected = true;
  assert.equal((await request('observe', {screenshot:'viewport'})).ok, false);
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
