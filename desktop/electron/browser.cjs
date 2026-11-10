const { BaseWindow, WebContentsView, net } = require('electron');
const { readFileSync } = require('node:fs');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

class BrowserHost {
  constructor({ root, backend, getWindow }) {
    this.backend = backend; this.getWindow = getWindow; this.views = new Map(); this.visible = null;
    this.surfaceRevision = 0;
    this.inputShield = null;
    this.backgroundWindow = null;
    this.localSessions = new Set();
    this.automation = readFileSync(path.join(root, 'web/editors/dist/browser-automation.js'), 'utf8');
  }
  async state(item) {
    if (item.view.webContents.isDestroyed()) return;
    const wc = item.view.webContents;
    await this.backend.call('browser.host_state', { conversation_id: item.cid, tab_id: item.id, title: wc.getTitle(), url: wc.getURL(), status: item.ready ? 'ready' : item.failed ? 'failed' : 'loading' });
  }
  create(cid, tab) {
    const view = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false, partition: 'persist:cowork-web-' + cid } });
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
    wc.setBackgroundThrottling(false);
    // Background automation needs a compositor surface. Parking views in a
    // hidden host keeps them live without covering another workspace/page.
    this.park(view);
    this.views.set(tab.id, item);
    wc.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    wc.session.setPermissionCheckHandler(() => false);
    wc.setWindowOpenHandler(({ url }) => { if (/^https?:\/\//i.test(url) && item.owner === 'human') void wc.loadURL(url).catch(() => {}); return { action: 'deny' }; });
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
  async js(item, code) {
    return item.view.webContents.executeJavaScriptInIsolatedWorld(1001, [{ code: this.automation + '\n' + code }]);
  }
  async handle(request) {
    let result;
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
        if (request.action === 'close') { if (this.visible === item) this.hide(); this.backgroundWindow?.contentView.removeChildView(item.view); wc.close(); this.views.delete(item.id); result = { ok: true }; }
        else if (request.action === 'navigate') { void wc.loadURL(args.url).catch(() => {}); result = { ok: true, status: 'loading' }; }
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
        else {
          if (!item.ready) throw new Error('网页尚未加载完成，请稍后重新观察。');
          const revision = item.revision;
          if (request.action === 'observe') {
            const value = await this.js(item, 'window.coworkObserve()');
            await this.checked(request);
            if (revision !== item.revision) throw new Error('页面已跳转，请重新观察。');
            result = { ...value, ok: true, tab_id: item.id, page_version: revision + ':' + value.dom_version };
            try {
              const screenshot = await wc.capturePage({ stayHidden: true, stayAwake: true });
              if (screenshot.isEmpty()) throw new Error('截图为空');
              result.screenshot = { type: 'image_url', image_url: { url: screenshot.toDataURL() } };
              result.image_status = 'ready';
            } catch (error) {
              result.image_status = 'failed';
              result.screenshot_note = '截图未完成：' + error.message + '。文字和元素结果已保留；坐标操作前请重新观察并取得截图。';
            }
            delete result.dom_version;
          } else if (request.action === 'browser_action') {
            const prefix = revision + ':';
            if (!String(args.page_version || '').startsWith(prefix)) throw new Error('页面已变化，请重新观察。');
            const operation = { ...args, dom_version: args.page_version.slice(prefix.length) };
            const native = ['click', 'click_at', 'key'].includes(args.action);
            const checked = await this.js(item, 'window.coworkAct(' + JSON.stringify({ ...operation, action: 'validate', target_action: args.action }) + ')');
            if (!checked?.ok) result = { ...checked, executed: false };
            else {
              await this.checked(request);
              if (revision !== item.revision) throw new Error('页面已跳转，请重新观察。');
              result = await this.js(item, 'window.coworkAct(' + JSON.stringify(native ? { ...operation, action: 'prepare_native', target_action: args.action } : operation) + ')');
              if (native && result?.ok) {
                await this.checked(request);
                if (revision !== item.revision) throw new Error('页面已跳转，请重新观察。');
                // Dispatch through the owned WebContents protocol so background
                // pages work without stealing focus or unlocking human input.
                if (!wc.debugger.isAttached()) wc.debugger.attach('1.3');
                if (args.action === 'key') {
                  const keys = { Enter: 13, Tab: 9, Escape: 27, Backspace: 8, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39 };
                  if (!keys[args.key]) throw new Error('不支持此按键。');
                  await wc.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'keyDown', key: args.key, windowsVirtualKeyCode: keys[args.key], ...(args.key === 'Enter' ? { text: '\r' } : {}) });
                  await wc.debugger.sendCommand('Input.dispatchKeyEvent', { type: 'keyUp', key: args.key, windowsVirtualKeyCode: keys[args.key] });
                } else {
                  const coords = args.action === 'click' ? result : args;
                  const x = Math.round(coords.x), y = Math.round(coords.y), bounds = item.view.getBounds();
                  if (!(x >= 0 && y >= 0 && x < bounds.width && y < bounds.height)) throw new Error('坐标超出网页，请重新观察。');
                  await wc.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
                  await wc.debugger.sendCommand('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
                }
                item.revision++;
                result = { ok: true, status: 'dispatched', verification: '请重新观察页面确认结果，勿重复执行。' };
              } else if (args.action === 'fill' && result?.ok) {
                await new Promise(resolve => setTimeout(resolve, 80));
                await this.checked(request);
                result = await this.js(item, 'window.coworkVerifyFill(' + JSON.stringify(operation) + ')');
              }
            }
          } else throw new Error('不支持的网页操作。');
        }
      }
    } catch (error) { result = { ok: false, status: 'failed', error: error.message, verification: '请检查实际页面后继续，操作不会自动重复。' }; }
    await this.backend.call('browser.host_result', { id: request.id, result }).catch(() => {});
  }
  hide() {
    this.surfaceRevision++;
    this.inputShield?.setVisible(false);
    if (this.visible) { this.park(this.visible.view); this.visible = null; }
  }
  park(view) {
    if (!this.backgroundWindow) this.backgroundWindow = new BaseWindow({ show: false, skipTaskbar: true, width: 1600, height: 1200 });
    this.getWindow()?.contentView.removeChildView(view);
    this.backgroundWindow.contentView.addChildView(view);
    view.setBounds({ ...view.getBounds(), x: 0, y: 0 });
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
    this.backgroundWindow?.contentView.removeChildView(item.view);
    window.contentView.addChildView(item.view); this.visible = item;
    item.view.setVisible(true);
    this.shield(window, item);
    return { ok: true, owner: item.owner };
  }
}
module.exports = { BrowserHost };
