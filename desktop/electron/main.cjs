const { app, BrowserWindow, ipcMain, dialog, shell, Menu, protocol, Tray, net, clipboard } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { Backend } = require('./backend.cjs');
const { PluginHost } = require('./plugins.cjs');
const { BrowserHost } = require('./browser.cjs');
const { FileHost } = require('./files.cjs');
const fileHost = new FileHost();
const { resolveDataDirectory } = require('./data-path.cjs');
protocol.registerSchemesAsPrivileged([{ scheme: 'cowork-local', privileges: { standard: true, secure: true, supportFetchAPI: true } }, require('./plugins.cjs').PLUGIN_SCHEME, { scheme: 'cowork-editor', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } }]);
const visualizationPages = new Map();
function servePlugin(plugin) {
  return { ...plugin, html: undefined, url: `cowork-plugin://${plugin.hash}/${plugin.entry}` };
}

app.setName('DeepSeek Cowork');
const root = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '../..');
const dataDir = resolveDataDirectory({ explicit: process.env.COWORK_APP_DATA_DIR,
  packaged: app.isPackaged, executable: app.getPath('exe'), repository: root, appData: app.getPath('appData') });
app.setPath('userData', path.join(dataDir, 'desktop-browser'));
const entry = pathToFileURL(path.join(__dirname, '../dist/index.html')).href;
const allowed = new Set(['app.bootstrap', 'conversation.create', 'conversation.list', 'conversation.get', 'conversation.rename',
  'conversation.archive', 'conversation.send', 'conversation.stop', 'conversation.steer', 'draft.save', 'view.save', 'view.get', 'interaction.respond',
  'settings.get', 'settings.save_model', 'settings.select_model', 'settings.permission', 'settings.theme', 'settings.context', 'workspace.list',
  'workspace.create', 'file.open', 'file.save', 'skills.list', 'workbench.result']);
for (const method of ['project.list', 'project.save', 'project.archive', 'project.restore', 'conversation.search',
  'conversation.pin', 'conversation.restore', 'conversation.delete', 'conversation.preferences', 'conversation.export',
  'conversation.reference', 'conversation.branch', 'history.prepare', 'history.edit', 'history.delete', 'history.undo',
  'agent.list', 'agent.get', 'agent.send', 'agent.stop', 'agent.resume', 'conversation.receipt', 'conversation.workspace']) allowed.add(method);
for (const method of require('./management-methods.cjs')) allowed.add(method);
allowed.add('task.inspect');
allowed.add('task.context');
allowed.add('host.recovery.get'); allowed.add('host.recovery.save');
allowed.add('visualization.get'); allowed.add('visualization.state');
for (const method of ['workbench.list', 'workbench.profiles', 'workbench.open', 'workbench.close', 'workbench.control', 'workbench.read', 'workbench.input', 'workbench.reply', 'workbench.resize', 'workbench.activate', 'file.save_copy']) allowed.add(method);
let window, backend, plugins, tray, browserHost, quitting = false, quitPending = false, uiReady = false;
let eventQueue = [], batch;
function publish(event) {
  if (event.event === 'browser_host') { void browserHost?.handle(event.data); return; }
  eventQueue.push(event);
  if (!batch) batch = setTimeout(() => {
    const events = eventQueue; eventQueue = []; batch = null;
    if (window && !window.isDestroyed()) window.webContents.send('cowork:events', events);
  }, 24);
}
async function quit({serviceStopped=false}={}) {
  if (quitting || quitPending) return;
  quitPending = true;
  try {
    if (serviceStopped) await backend.waitStopped();
    else await backend.stop();
    backend.detach(); quitting = true; tray?.destroy(); app.quit();
  } finally { quitPending = false; }
}
async function requestExit() {
  window?.show();
  if (uiReady) { publish({event:'close_requested'}); return; }
  try { await quit(); }
  catch(error) { await dialog.showMessageBox(window,{type:'error',title:'尚未完成退出',message:error.message,detail:'应用保持打开，已有数据仍保留。'}); }
}
async function createWindow() {
  window = new BrowserWindow({ width: 1400, height: 930, minWidth: 850, minHeight: 620,
    backgroundColor: '#fafafa', title: 'DeepSeek Cowork', show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false, webviewTag: false } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('context-menu', (_event, params) => {
    if (!params.isEditable && !params.selectionText) return;
    const flags = params.editFlags;
    const actions = params.isEditable ? [
      {label:'撤销', role:'undo', enabled:flags.canUndo},
      {label:'重做', role:'redo', enabled:flags.canRedo},
      {type:'separator'},
      {label:'剪切', role:'cut', enabled:flags.canCut},
      {label:'复制', role:'copy', enabled:flags.canCopy},
      {label:'粘贴', role:'paste', enabled:flags.canPaste},
      {type:'separator'},
      {label:'全选', role:'selectAll', enabled:flags.canSelectAll},
    ] : [{label:'复制', role:'copy', enabled:flags.canCopy}];
    Menu.buildFromTemplate(actions).popup({window, frame:params.frame || undefined});
  });
  window.webContents.on('did-start-loading', () => { uiReady = false; });
  window.webContents.on('will-navigate', (event, url) => { if (url !== entry) event.preventDefault(); });
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  window.once('ready-to-show', () => window.show());
  window.on('close', event => {
    if (quitting) return;
    event.preventDefault();
    window.hide();
    window.webContents.send('cowork:events', [{ event: 'window_hidden' }]);
  });
  await window.loadURL(entry);
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { window.restore(); window.show(); window.focus(); } });
  app.whenReady().then(async () => {
    protocol.handle('cowork-local', request => fileHost.response(request, net.fetch));
    protocol.handle('cowork-editor', request => {
      const url = new URL(request.url);
      const filename = decodeURIComponent(url.pathname.slice(1));
      if (url.host !== 'builtin' || !/^[a-zA-Z0-9_.-]+$/.test(filename)) return new Response('Not found', { status: 404 });
      return net.fetch(pathToFileURL(path.join(root, 'web/editors/dist', filename)).href);
    });
    protocol.handle('cowork-plugin', request => {
      const url = new URL(request.url);
      if (url.host === 'visualization') {
        const html = visualizationPages.get(url.pathname.slice(1));
        return new Response(html || 'Not found', {status:html ? 200:404,headers:{'Content-Type':'text/html; charset=utf-8'}});
      }
      const asset = plugins?.asset(url.host, decodeURIComponent(url.pathname.slice(1)));
      return new Response(asset?.body || 'Plugin resource not available', {status:asset ? 200:404,
        headers:{'Content-Type':asset?.type || 'text/plain','Access-Control-Allow-Origin':'*','X-Content-Type-Options':'nosniff'}});
    });
    Menu.setApplicationMenu(Menu.buildFromTemplate([{ label: 'DeepSeek Cowork', submenu: [
      { label: '重新载入界面', accelerator: 'CmdOrCtrl+R', click: () => publish({ event: 'reload_requested' }) },
      { label: '恢复内置界面', click: () => publish({event:'plugins_recover_requested'}) },
      { label: '打开诊断日志', click: () => shell.openPath(path.join(dataDir, 'logs')) },
      { type: 'separator' }, { label: '退出应用', click: () => void requestExit() }
    ] }, { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
      { label: '视图', submenu: [{ role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }] }]));
    plugins = new PluginHost(dataDir, path.join(__dirname, '../plugins/writing'));
    try { backend = new Backend({ root, dataDir, resources: app.isPackaged ? process.resourcesPath : null, publish }); }
    catch (error) { await dialog.showMessageBox({ type: 'error', message: error.message }); quitting = true; app.quit(); return; }
    backend.ready.catch(error => publish({ event: 'service_error', error: error.message }));
    browserHost = new BrowserHost({ root, backend, getWindow: () => window });
    tray = new Tray(path.join(root, app.isPackaged ? 'logo.ico' : 'images/logo.ico'));
    tray.setToolTip('DeepSeek Cowork');
    const showWindow = () => { window?.restore(); window?.show(); window?.focus(); };
    tray.on('double-click', showWindow);
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: '打开 DeepSeek Cowork', click: showWindow },
      { type: 'separator' },
      { label: '退出应用', click: () => { showWindow(); void requestExit(); } }
    ]));
    ipcMain.handle('cowork:call', async (event, method, params = {}) => {
      if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== entry) throw new Error('无效的界面连接。');
      if (typeof method !== 'string' || typeof params !== 'object' || params === null) throw new Error('无效请求。');
      if (['file.open', 'file.save_copy', 'workspace.create'].includes(method)) return fileHost.register(await backend.call(method, params));
      if (method === 'file.external') {
        const file = await backend.call('file.describe', params);
        return shell.openPath(file.path);
      }
      if (method === 'file.reveal') { const file = await backend.call('file.describe', params); shell.showItemInFolder(file.path); return { revealed: true }; }
      if (method === 'file.describe') return backend.call(method, params);
      if (method === 'file.preview_draft') return fileHost.draft(params.url, params.text);
      if (method === 'visualization.get') {
        const value = await backend.call(method, params);
        visualizationPages.set(value.token, value.html);
        return {...value, html: undefined, url: `cowork-plugin://visualization/${value.token}`};
      }
      if (method === 'visualization.release') { visualizationPages.delete(params.token); return {released:true}; }
      if (allowed.has(method)) return backend.call(method, params);
      if (['browser.open', 'browser.navigate', 'browser.close', 'browser.command'].includes(method)) return backend.call(method, params);
      if (method === 'browser.surface') return browserHost.show(params);
      if (method === 'clipboard.read') return clipboard.readText();
      if (method === 'clipboard.write') { await clipboard.writeText(String(params.text || '')); return { ok: true }; }
      if (method === 'dialog.choose') {
        const filters = params.kind === 'skill' ? [{ name: '技能包', extensions: ['zip'] }]
          : params.kind === 'theme' ? [{ name: '主题', extensions: ['cowork-theme', 'json'] }] : [];
        const result = await dialog.showOpenDialog(window, { title: '选择文件', filters, properties: ['openFile'] });
        return result.canceled ? null : result.filePaths[0];
      }
      if (method === 'dialog.save') {
        const result = await dialog.showSaveDialog(window, { title: '保存文件', defaultPath: path.basename(String(params.name || '导出文件')) });
        return result.canceled ? null : result.filePath;
      }
      if (method === 'dialog.workspace') {
        const result = await dialog.showOpenDialog(window, { title: '选择工作目录', properties: ['openDirectory', 'createDirectory'] });
        return result.canceled ? null : result.filePaths[0];
      }
      if (method === 'dialog.attach') {
        const result = await dialog.showOpenDialog(window, { title: '添加资料', properties: ['openFile', 'multiSelections'] });
        return result.canceled ? [] : backend.call('workspace.attach', { ...params, paths: result.filePaths });
      }
      if (method === 'dialog.import_settings') {
        const result = await dialog.showOpenDialog(window, { title: '导入旧版模型设置', defaultPath: path.join(app.getPath('appData'), 'DeepSeekCowork', 'config.json'), filters: [{ name: '配置文件', extensions: ['json'] }], properties: ['openFile'] });
        return result.canceled ? null : backend.call('settings.import', { path: result.filePaths[0] });
      }
      if (method.startsWith('plugins.')) await backend.ready;
      if (method === 'plugins.list') return plugins.list();
      if (method === 'plugins.inventory') return plugins.inventory();
      if (method === 'plugins.load') return servePlugin(await plugins.load(params.id));
      if (method === 'plugins.preview') {
        const result = await dialog.showOpenDialog(window, { title: '选择插件源代码文件夹', properties: ['openDirectory'] });
        return result.canceled ? null : servePlugin(await plugins.preview(result.filePaths[0]));
      }
      if (method === 'plugins.install') return plugins.install(params.token);
      if (method === 'plugins.stage') return servePlugin(await plugins.stage(params.token));
      if (method === 'plugins.discard') { plugins.discard(params.token); return {discarded:true}; }
      if (method === 'plugins.rollback_preview') return servePlugin(await plugins.rollbackPreview(params.id));
      if (method === 'plugins.enable_preview') return servePlugin(await plugins.enablePreview(params.id));
      if (method === 'plugins.recover') return plugins.recover();
      if (method === 'plugins.disable') return plugins.disable(params.id);
      if (method === 'plugins.rollback') return plugins.rollback(params.id);
      if (method === 'plugins.export') {
        const result = await dialog.showOpenDialog(window, { title: '将可编辑插件源码保存到工作目录', properties: ['openDirectory', 'createDirectory'] });
        return result.canceled ? null : plugins.export(params.id, result.filePaths[0]);
      }
      if (method === 'app.quit') { await quit(); return { accepted: true }; }
      if (method === 'app.ui_ready') { uiReady = true; return {ready:true}; }
      if (method === 'app.startup_status') return backend.startupState;
      if (method === 'app.reload') { window.webContents.reload(); return { accepted: true }; }
      if (method === 'app.reconnect') { backend.ready = backend.start(); await backend.ready; publish({ event: 'service_reconnected' }); return { connected: true }; }
      if (method === 'app.ready') return backend.call('updates.acknowledge');
      if (method === 'app.install_update') {
        let transaction;
        try {
          transaction = await backend.call('updates.prepare_install', { host_pid: process.pid });
          await backend.call('updates.commit_install', transaction);
          await quit({serviceStopped:true});
          return { accepted: true };
        } catch (error) {
          if (transaction) await backend.call('updates.abort_install', transaction).catch(() => {});
          throw error;
        }
      }
      if (method === 'app.logs') return shell.openPath(path.join(dataDir, 'logs'));
      if (method === 'system.open_path') {
        const base = await fs.realpath(dataDir), target = await fs.realpath(String(params.path || ''));
        const relative = path.relative(base, target);
        if (relative.startsWith('..') || path.isAbsolute(relative) || !(await fs.stat(target)).isDirectory()) throw new Error('只能打开应用的数据目录。');
        const error = await shell.openPath(target);
        if (error) throw new Error(error);
        return { opened: true };
      }
      if (method === 'external.open') {
        const url = new URL(params.url);
        if (!['https:', 'http:'].includes(url.protocol)) throw new Error('链接协议不受支持。');
        return shell.openExternal(url.href);
      }
      throw new Error('不支持的桌面操作。');
    });
    await createWindow();
  });
}
app.on('before-quit', event => { if (!quitting && backend) { event.preventDefault(); void requestExit(); } });
app.on('window-all-closed', () => { if (quitting) app.quit(); });
