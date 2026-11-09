const { app, BrowserWindow, ipcMain, dialog, shell, Menu, protocol } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { Backend } = require('./backend.cjs');
const { PluginHost } = require('./plugins.cjs');
protocol.registerSchemesAsPrivileged([{ scheme: 'cowork-plugin', privileges: { standard: true, secure: true } }]);
const pluginPages = new Map();
function servePlugin(plugin) {
  pluginPages.set(plugin.hash, plugin.html);
  return { ...plugin, html: undefined, url: `cowork-plugin://editor/${plugin.hash}` };
}

const root = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '../..');
const dataDir = process.env.COWORK_APP_DATA_DIR || path.join(app.getPath('appData'), 'DeepSeekCoworkNext');
app.setPath('userData', dataDir);
const entry = pathToFileURL(path.join(__dirname, '../dist/index.html')).href;
const allowed = new Set(['app.bootstrap', 'conversation.create', 'conversation.list', 'conversation.get', 'conversation.rename',
  'conversation.archive', 'conversation.send', 'conversation.stop', 'conversation.steer', 'draft.save', 'view.save', 'view.get', 'interaction.respond',
  'settings.get', 'settings.save_model', 'settings.select_model', 'settings.permission', 'settings.theme', 'settings.context', 'workspace.list',
  'workspace.create', 'file.open', 'file.save', 'skills.list', 'workbench.result']);
let window, backend, plugins, quitting = false;
let eventQueue = [], batch;
function publish(event) {
  eventQueue.push(event);
  if (!batch) batch = setTimeout(() => {
    const events = eventQueue; eventQueue = []; batch = null;
    if (window && !window.isDestroyed()) window.webContents.send('cowork:events', events);
  }, 24);
}
async function quit() {
  if (quitting) return;
  try {
    await backend.stop(); quitting = true; app.quit();
  } catch (error) { await dialog.showMessageBox(window, { type: 'error', title: '尚未完成退出', message: error.message, detail: '应用保持打开，已完成内容和草稿不会被清除。' }); }
}
async function createWindow() {
  window = new BrowserWindow({ width: 1400, height: 930, minWidth: 850, minHeight: 620,
    backgroundColor: '#fafafa', title: 'Cowork', show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false, webviewTag: false } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (url !== entry) event.preventDefault(); });
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  window.once('ready-to-show', () => window.show());
  window.on('close', event => {
    if (quitting) return;
    event.preventDefault();
    window.webContents.send('cowork:events', [{ event: 'close_requested' }]);
  });
  await window.loadURL(entry);
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { window.restore(); window.show(); window.focus(); } });
  app.whenReady().then(async () => {
    protocol.handle('cowork-plugin', request => {
      const url = new URL(request.url);
      const html = url.host === 'editor' && pluginPages.get(url.pathname.slice(1));
      return new Response(html || 'Plugin page not available', { status: html ? 200 : 404, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    });
    Menu.setApplicationMenu(Menu.buildFromTemplate([{ label: 'Cowork', submenu: [
      { label: '重新载入界面', accelerator: 'CmdOrCtrl+R', click: () => publish({ event: 'reload_requested' }) },
      { label: '打开诊断日志', click: () => shell.openPath(path.join(dataDir, 'logs')) },
      { type: 'separator' }, { label: '退出', accelerator: 'Alt+F4', click: () => window.close() }
    ] }, { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
      { label: '视图', submenu: [{ role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }] }]));
    plugins = new PluginHost(dataDir, path.join(__dirname, '../plugins/writing'));
    try { backend = new Backend({ root, dataDir, resources: app.isPackaged ? process.resourcesPath : null, publish }); }
    catch (error) { await dialog.showMessageBox({ type: 'error', message: error.message }); quitting = true; app.quit(); return; }
    backend.ready.catch(error => publish({ event: 'service_error', error: error.message }));
    ipcMain.handle('cowork:call', async (event, method, params = {}) => {
      if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== entry) throw new Error('无效的界面连接。');
      if (typeof method !== 'string' || typeof params !== 'object' || params === null) throw new Error('无效请求。');
      if (allowed.has(method)) return backend.call(method, params);
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
      if (method === 'plugins.list') return plugins.list();
      if (method === 'plugins.load') return servePlugin(await plugins.load(params.id));
      if (method === 'plugins.preview') {
        const result = await dialog.showOpenDialog(window, { title: '选择插件源代码文件夹', properties: ['openDirectory'] });
        return result.canceled ? null : servePlugin(await plugins.preview(result.filePaths[0]));
      }
      if (method === 'plugins.install') return plugins.install(params.token);
      if (method === 'plugins.rollback') return plugins.rollback(params.id);
      if (method === 'plugins.export') {
        const result = await dialog.showOpenDialog(window, { title: '将可编辑插件源码保存到工作目录', properties: ['openDirectory', 'createDirectory'] });
        return result.canceled ? null : plugins.export(params.id, result.filePaths[0]);
      }
      if (method === 'app.quit') { void quit(); return { accepted: true }; }
      if (method === 'app.reload') { window.webContents.reload(); return { accepted: true }; }
      if (method === 'app.logs') return shell.openPath(path.join(dataDir, 'logs'));
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
app.on('before-quit', event => { if (!quitting && backend) { event.preventDefault(); window?.close(); } });
app.on('window-all-closed', () => { if (quitting) app.quit(); });
