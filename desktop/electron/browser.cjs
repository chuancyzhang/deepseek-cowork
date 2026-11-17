const { BaseWindow, WebContentsView, nativeImage, net } = require('electron');
const { readFileSync } = require('node:fs');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const { BrowserAutomation } = require('./browser-automation.cjs');

class BrowserHost {
  constructor({ root, backend, getWindow, diagnostic = () => {} }) {
    this.backend = backend; this.getWindow = getWindow; this.views = new Map(); this.visible = null;
    this.diagnostic = diagnostic;
    this.surfaceRevision = 0;
    this.inputShield = null;
    this.backgroundWindows = new Map();
    this.localSessions = new Set();
    this.controller = new BrowserAutomation(this);
    this.automation = readFileSync(path.join(root, 'web/editors/dist/browser-automation.js'), 'utf8');
  }
  async state(item) {
    await item.registration;
    if (item.view.webContents.isDestroyed()) return;
    const wc = item.view.webContents;
    await this.backend.call('browser.host_state', { conversation_id: item.cid, tab_id: item.id, title: wc.getTitle(), url: wc.getURL(), status: item.ready ? 'ready' : item.failed ? 'failed' : 'loading' });
  }
  create(cid, tab, options={}) {
    const view = new WebContentsView({ webPreferences: { ...options.webPreferences, sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false, partition: 'persist:cowork-web-' + cid }, ...(options.webContents ? {webContents:options.webContents} : {}) });
    const wc = view.webContents;
    const item = { id: tab.id, cid, view, revision: 0, ready: false, failed: false, owner: tab.owner };
    item.localRoot = tab.path ? path.dirname(tab.path) : null;
    item.startUrl = tab.path ? `cowork-local://${tab.id}/${encodeURIComponent(path.basename(tab.path))}` : tab.url;
    if (!this.localSessions.has(cid)) {
      this.localSessions.add(cid);
      wc.session.protocol.handle('cowork-local', async request => {
        try {
          const url = new URL(request.url), target = this.views.get(url.host);
          if (!target?.localRoot || target.cid !== cid) return new Response('Not found', { status: 404 });
          const root = await fs.realpath(target.localRoot);
          const file = await fs.realpath(path.resolve(root, '.' + decodeURIComponent(url.pathname)));
          const relative = path.relative(root, file);
          if (relative.startsWith('..') || path.isAbsolute(relative)) return new Response('Forbidden', { status: 403 });
          return net.fetch(pathToFileURL(file).href);
        } catch { return new Response('Not found', { status: 404 }); }
      });
    }
    view.setBounds({ x: 0, y: 0, width: 1000, height: 750 });
    // Background automation needs a live compositor, even when no tab is shown.
    this.park(view);
    wc.setBackgroundThrottling(false);
    this.views.set(tab.id, item);
    item.protocolReady = this.controller.attach(item);
    item.protocolReady.catch(error => this.diagnostic('browser_protocol_failed', {tab_id:item.id,error:error.message}));
    wc.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    wc.session.setPermissionCheckHandler(() => false);
    wc.setWindowOpenHandler(({url}) => {
      if ((!/^https?:\/\//i.test(url)&&url!=='about:blank') || item.popups.length >= 10) return {action:'deny'};
      // Let Chromium preserve opener, POST body and named-window behavior. The
      // surface stays inside the owning chat; it never becomes a native window.
      return {action:'allow',outlivesOpener:true,createWindow:options=>{
        const popup=this.create(cid,{id:'pending-'+randomUUID(),owner:item.owner,url},options);
        const entry={tab_id:popup.id,url};item.popups.push(entry);
        popup.registration=this.backend.call('browser.host_popup',{conversation_id:cid,tab_id:item.id,url}).then(({tab:child})=>{
          this.views.delete(popup.id);popup.id=child.id;entry.tab_id=child.id;
          if(!popup.view.webContents.isDestroyed())this.views.set(child.id,popup);
        });
        popup.registration.then(()=>this.state(popup)).catch(error=>{
          this.diagnostic('browser_popup_failed',{tab_id:item.id,error:error.message});
          if(!popup.view.webContents.isDestroyed())popup.view.webContents.close();
        });
        popup.view.webContents.once('destroyed',()=>{
          this.release(popup);
          if(!popup.toolClosing)void popup.registration.then(()=>this.backend.call('browser.host_closed',{conversation_id:cid,tab_id:popup.id})).catch(error=>this.diagnostic('browser_popup_close_failed',{tab_id:popup.id,error:error.message}));
        });
        return popup.view.webContents;
      }};
    });
    wc.on('will-navigate', (event, url) => { if (!/^https?:\/\//i.test(url) && !(item.localRoot && url.startsWith(`cowork-local://${tab.id}/`))) event.preventDefault(); });
    wc.on('did-start-navigation', (_event, _url, _inPlace, isMainFrame) => { if (isMainFrame) { item.ready = false; item.failed = false; item.revision++; } });
    wc.on('did-finish-load', () => { item.ready = true; void this.state(item).catch(() => {}); });
    wc.on('did-fail-load', (_event, code) => { if (code !== -3) { item.failed = true; void this.state(item).catch(() => {}); } });
    wc.on('page-title-updated', () => { void this.state(item).catch(() => {}); });
    const notify = (event, data = {}) => this.getWindow()?.webContents.send('cowork:events', [{event, conversation_id:cid, data:{tab_id:item.id,...data}}]);
    wc.on('found-in-page', (_event, result) => notify('browser_matches', result));
    wc.on('before-input-event', (event, input) => {
      if (item.owner === 'human' && input.type === 'keyDown' && (input.control || input.meta) && input.key.toLowerCase() === 'f') {
        event.preventDefault(); this.getWindow()?.webContents.focus(); notify('browser_find');
      }
    });
    // Native input interception also blocks CDP input. An independent surface
    // catches human input while the owned page remains live and automatable.
    wc.on('focus', () => { if (item.owner !== 'human') this.getWindow()?.webContents.focus(); });
    return item;
  }
  async checked(request) { return this.backend.call('browser.host_check', { id: request.id }); }
  release(item) {
    if(this.visible===item)this.hide();
    const background=this.backgroundWindows.get(item.view);
    if(background&&!background.isDestroyed()){background.contentView.removeChildView(item.view);background.destroy();}
    this.backgroundWindows.delete(item.view);this.views.delete(item.id);
  }
  async capture(wc) {
    if (!wc.debugger.isAttached()) wc.debugger.attach('1.3');
    let timer;
    try {
      // CDP waits for a compositor frame; capturePage can fail immediately just
      // after navigation or while another background view covers this one.
      const { data } = await Promise.race([
        wc.debugger.sendCommand('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('等待网页画面超时')), 8000); }),
      ]);
      return nativeImage.createFromBuffer(Buffer.from(data, 'base64'));
    } finally { clearTimeout(timer); }
  }
  async handle(request) {
    let result;
    const started = Date.now();
    const context = { request_id: request.id, conversation_id: request.conversation_id, tab_id: request.tab.id, action: request.action };
    this.diagnostic('browser_request_started', context);
    try {
      await this.checked(request);
      let item = this.views.get(request.tab.id);
      if (request.action === 'open') {
        item = this.create(request.conversation_id, request.tab);
        // Navigation has an observable load state; no action replay on failure.
        void item.view.webContents.loadURL(item.startUrl).catch(() => {});
        result = { ok: true, status: 'loading' };
      } else {
        if (!item || item.cid !== request.conversation_id) throw new Error('网页已关闭，请重新打开。');
        const wc = item.view.webContents, args = request.arguments;
        item.owner = request.tab.owner;
        if (request.action === 'close') {
          item.toolClosing=true;wc.close();this.release(item);
          result = { ok: true };
        }
        else if (request.action === 'navigate') {
          const navigation=wc.navigationHistory,mode=args.navigation||'url';
          if((mode==='back'&&!navigation.canGoBack())||(mode==='forward'&&!navigation.canGoForward()))throw Error('没有可用的历史页面。');
          await this.controller.dispatch(request,item,'navigate',0);
          if(mode==='back')navigation.goBack();else if(mode==='forward')navigation.goForward();else if(mode==='reload')wc.reload();else void wc.loadURL(args.url).catch(()=>{});
          result=await this.controller.receipt(request,{ok:true,status:'dispatched',loading:true});
        }
        else if (request.action === 'command') {
          const navigation = wc.navigationHistory;
          if (args.command === 'back' && navigation.canGoBack()) navigation.goBack();
          else if (args.command === 'forward' && navigation.canGoForward()) navigation.goForward();
          else if (args.command === 'reload') wc.reload();
          else if (args.command === 'find') { if (String(args.text || '').trim()) wc.findInPage(String(args.text).slice(0, 1000), {forward: !args.reverse, findNext: Boolean(args.next)}); item.revision++; }
          else if (args.command === 'stop_find') wc.stopFindInPage('clearSelection');
          else if (args.command === 'zoom') { const zoom = Number(args.value); if (!Number.isFinite(zoom) || zoom < .25 || zoom > 3) throw new Error('网页缩放应在 25% 到 300% 之间。'); wc.setZoomFactor(zoom); item.revision++; }
          result = {ok:true, canBack:navigation.canGoBack(), canForward:navigation.canGoForward(), zoom:wc.getZoomFactor()};
        }
        else result = await this.controller.handle(request, item);
      }
    } catch (error) {
      result = {ok:false,status:request.dispatched || request.lastReceipt ? 'unknown' : 'failed',executed:Boolean(request.dispatched || request.lastReceipt),error:error.message,verification:'请核对实际页面和动作回执，不要自动重复操作。'};
      if(request.dispatched || request.lastReceipt) {
        result = {...request.lastReceipt,...result,effect_state:'unknown'};
        try { if(request.dispatched) result = await this.controller.receipt(request,result);
          else await this.backend.call('browser.host_receipt',{id:request.id,result});
        } catch(saveError) {result.receipt_error=saveError.message;}
      }
    }
    this.diagnostic('browser_request_finished', { ...context, elapsed_ms: Date.now() - started, ok: result.ok, status: result.status, image_status: result.image_status, error: result.error });
    await this.backend.call('browser.host_result', { id: request.id, result }).catch(error => this.diagnostic('browser_result_rejected', { ...context, error: error.message }));
  }
  hide() {
    this.surfaceRevision++;
    this.inputShield?.setVisible(false);
    if (this.visible) { this.park(this.visible.view); this.visible = null; }
  }
  park(view) {
    let background = this.backgroundWindows.get(view);
    if (!background) {
      // A never-shown native window has no reliable capture/input surface on
      // Windows. Keep it composited, but invisible and unable to take input.
      // Each tab gets its own compositor: overlapping sibling views can stop
      // painting independently, even with background throttling disabled.
      background = new BaseWindow({ show: false, opacity: 0, focusable: false, frame: false, skipTaskbar: true });
      background.setIgnoreMouseEvents(true);
      this.backgroundWindows.set(view, background);
    }
    this.getWindow()?.contentView.removeChildView(view);
    background.contentView.addChildView(view);
    const { width, height } = view.getBounds();
    background.setContentSize(width, height);
    view.setBounds({ x: 0, y: 0, width, height });
    background.showInactive();
  }
  shield(window, item) {
    if (item.owner === 'human') return;
    if (!this.inputShield) {
      this.inputShield = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
      this.inputShield.setBackgroundColor('#00000000');
      const html = '<!doctype html><meta charset="utf-8"><html style="background:transparent"><body style="margin:0;cursor:default" aria-label="AI 正在控制网页，请先接管"></body></html>';
      void this.inputShield.webContents.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
    }
    this.inputShield.setBounds(item.view.getBounds());
    window.contentView.addChildView(this.inputShield);
    this.inputShield.setVisible(true);
    if (item.view.webContents.isFocused()) window.webContents.focus();
  }
  async show(params) {
    this.hide();
    const generation = this.surfaceRevision;
    if (!params.tab_id) return { ok: true };
    const item = this.views.get(params.tab_id);
    if (!item || item.cid !== params.conversation_id) throw new Error('网页已关闭，请重新打开。');
    const snapshot = await this.backend.call('workbench.list', { conversation_id: item.cid });
    if (generation !== this.surfaceRevision) return { ok: true };
    const tab = snapshot.tabs.find(tab => tab.id === item.id);
    if (!tab) throw new Error('网页已关闭。');
    item.owner = tab.owner;
    const window = this.getWindow();
    const bounds = window.getContentBounds(), rect = params.bounds || {};
    const x = Math.max(0, Math.round(rect.x || 0)), y = Math.max(0, Math.round(rect.y || 0));
    item.view.setBounds({ x, y, width: Math.max(1, Math.min(bounds.width - x, Math.round(rect.width || 1))), height: Math.max(1, Math.min(bounds.height - y, Math.round(rect.height || 1))) });
    // The transparent input surface leaves the live page visible after handoff.
    this.backgroundWindows.get(item.view)?.contentView.removeChildView(item.view);
    window.contentView.addChildView(item.view); this.visible = item;
    item.view.setVisible(true);
    this.shield(window, item);
    return { ok: true, owner: item.owner };
  }
}
module.exports = { BrowserHost };
