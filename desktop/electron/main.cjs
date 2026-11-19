const { app, BrowserWindow, ipcMain, dialog, shell, Menu, protocol, Tray, net, clipboard } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { Backend } = require('./backend.cjs');
const {applicationMenu} = require('./application-menu.cjs');
const { PluginHost } = require('./plugins.cjs');
const { CreationPreview } = require('./creation-preview.cjs');
const { BrowserHost } = require('./browser.cjs');
const { FileHost } = require('./files.cjs');
const fileHost = new FileHost();
const { resolveDataDirectory } = require('./data-path.cjs');
const {prepareInstall,commitInstall,startupRequest,UpdateStartup} = require('./updates.cjs');
protocol.registerSchemesAsPrivileged([{ scheme: 'cowork-local', privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true } }, require('./plugins.cjs').PLUGIN_SCHEME, { scheme: 'cowork-editor', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } }]);
const visualizationPages = new Map();
const {SnippetHost} = require('./snippets.cjs');
const snippetHost = new SnippetHost({diagnostic:(event,details)=>{
  void fs.mkdir(path.join(dataDir,'logs'),{recursive:true})
    .then(()=>fs.appendFile(path.join(dataDir,'logs','desktop-preview.log'),`${new Date().toISOString()} ${event} ${JSON.stringify(details)}\n`))
    .catch(error=>console.error('preview_diagnostic_write_failed',error.message));
}});
function servePlugin(plugin) {
  return { ...plugin, html: undefined, url: plugin.entry ? `cowork-plugin://${plugin.hash}/${plugin.entry}` : undefined };
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
  'workspace.create', 'workspace.attach', 'workspace.attach_data', 'file.open', 'file.save', 'skills.list', 'workbench.result']);
for (const method of ['project.list', 'project.save', 'project.archive', 'project.restore', 'conversation.search',
  'sidebar.create_group', 'sidebar.move', 'sidebar.pin_project',
  'conversation.pin', 'conversation.restore', 'conversation.delete', 'conversation.preferences', 'conversation.export',
  'conversation.reference', 'conversation.branch', 'history.prepare', 'history.edit', 'history.delete', 'history.undo',
  'agent.list', 'agent.get', 'agent.send', 'agent.stop', 'agent.resume', 'conversation.receipt', 'conversation.workspace']) allowed.add(method);
for (const method of require('./management-methods.cjs')) allowed.add(method);
allowed.add('task.inspect');
allowed.add('updates.abort_install');
allowed.add('task.context');
for (const method of ['file.render', 'file.render_result', 'file.install_renderer']) allowed.add(method);
for (const method of ['reviews.create', 'reviews.list', 'reviews.get', 'reviews.update', 'reviews.generate', 'reviews.open', 'reviews.save', 'reviews.rebase']) allowed.add(method);
allowed.add('host.recovery.get'); allowed.add('host.recovery.save');
for (const method of ['modes.list', 'modes.enable', 'creation.candidate']) allowed.add(method);
allowed.add('visualization.get'); allowed.add('visualization.state');
for (const method of ['workbench.list', 'workbench.profiles', 'workbench.open', 'workbench.close', 'workbench.control', 'workbench.read', 'workbench.input', 'workbench.reply', 'workbench.resize', 'workbench.activate', 'file.save_copy']) allowed.add(method);
let window, backend, plugins, tray, browserHost, creationPreview, quitting = false, quitPending = false, uiReady = false;
let eventQueue = [], batch;
const startup = new UpdateStartup({installDir: app.isPackaged ? path.dirname(app.getPath('exe')) : root,
  version:app.getVersion(), call:(...args)=>backend.call(...args), diagnostic:(event,details)=>{
    void fs.mkdir(path.join(dataDir,'logs'),{recursive:true})
      .then(()=>fs.appendFile(path.join(dataDir,'logs','update.log'),`${new Date().toISOString()} ${event} ${JSON.stringify(details)}\n`))
      .catch(error=>console.error('update_diagnostic_write_failed',error.message));
  }});
const initialStartupRequest = startupRequest(process.env, startup.installDir);
startup.accept(initialStartupRequest);
let installPending = false;
function publish(event) {
  if (event.event === 'browser_host') { void browserHost?.handle(event.data); return; }
  if (event.event === 'creation_preview_request') {
    void creationPreview.handle(event).catch(error => console.warn('creation_preview_transport_failed', {conversation_id:event.conversation_id,run_id:event.run_id,message:error.message}));
    return;
  }
  if (event.event === 'creation_preview_cancelled') { creationPreview.cancel(event.data.id); return; }
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
    backgroundColor: '#fafafa', title: '', show: false, autoHideMenuBar:true,
    ...(process.platform === 'win32' ? {titleBarStyle:'hidden', titleBarOverlay:{color:'#fafafa',symbolColor:'#303030',height:40}} : {}),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false, webviewTag: false } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.setMenuBarVisibility(false);
  snippetHost.attach(window.webContents);
  window.webContents.on('console-message', details => {
    if (!details.message?.startsWith('workbench_render_failed ')) return;
    // Persist the boundary diagnostic only, never general page console output.
    void fs.mkdir(path.join(dataDir, 'logs'), {recursive:true})
      .then(() => fs.appendFile(path.join(dataDir, 'logs', 'desktop-renderer.log'), `${new Date().toISOString()} ${details.message}\n`))
      .catch(error => console.error('renderer_diagnostic_write_failed', error.message));
  });
  window.on('page-title-updated', event => event.preventDefault());
  window.webContents.on('context-menu', (_event, params) => {
    if (!params.isEditable && !params.selectionText && params.mediaType !== 'image') return;
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
    ] : params.selectionText ? [{label:'复制', role:'copy', enabled:flags.canCopy}] : [];
    if (params.mediaType === 'image') {
      if (actions.length) actions.push({type:'separator'});
      actions.push({label:'复制图片', enabled:params.hasImageContents,
        click:() => window.webContents.copyImageAt(params.x, params.y)});
    }
    Menu.buildFromTemplate(actions).popup({window, frame:params.frame || undefined});
  });
  window.webContents.on('did-start-loading', () => { uiReady = false; startup.ready = false; });
  window.webContents.on('will-navigate', (event, url) => { if (url !== entry) event.preventDefault(); });
  window.webContents.on('will-frame-navigate', event => {
    const current = event.frame?.url;
    if (current?.startsWith('cowork-plugin:') && !event.url.startsWith('cowork-plugin://'+new URL(current).host+'/')) {
      event.preventDefault();
    }
  });
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
if (!app.requestSingleInstanceLock({update:initialStartupRequest})) app.quit();
else {
  app.on('second-instance', (_event,_argv,_cwd,data) => {
    if (window) { window.restore(); window.show(); window.focus(); }
    startup.accept(data?.update);
    void startup.flush().catch(error=>publish({event:'service_error',error:error.message}));
  });
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
      if (url.hostname.startsWith('snippet-')) return snippetHost.response(url);
      if (url.host === 'visualization') {
        const html = visualizationPages.get(url.pathname.slice(1));
        return new Response(html || 'Not found', {status:html ? 200:404,headers:{'Content-Type':'text/html; charset=utf-8'}});
      }
      const asset = plugins?.asset(url.host, decodeURIComponent(url.pathname.slice(1)));
      return new Response(asset?.body || 'Plugin resource not available', {status:asset ? 200:404,
        headers:{'Content-Type':asset?.type || 'text/plain','Access-Control-Allow-Origin':'*','X-Content-Type-Options':'nosniff'}});
    });
    const menuFailure = error => {
      console.error('application_menu_failed', error.message);
      publish({event:'navigation_error',error:error.message});
    };
    Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenu({
      command: command => publish({event:'navigation_command',data:{command}}),
      exit: () => void requestExit(),
      logs: () => void shell.openPath(path.join(dataDir,'logs')).then(error => {if(error)throw Error(error);}).catch(menuFailure),
      guide: () => void shell.openExternal('https://github.com/chuancyzhang/deepseek-cowork/blob/main/docs/user-guide.md').catch(menuFailure),
      recover: () => publish({event:'plugins_recover_requested'}),
      reload: () => publish({event:'reload_requested'}),
    })));
    plugins = new PluginHost(dataDir, path.join(__dirname, '../plugins/writing'));
    try { backend = new Backend({ root, dataDir, resources: app.isPackaged ? process.resourcesPath : null, publish }); }
    catch (error) { await dialog.showMessageBox({ type: 'error', message: error.message }); quitting = true; app.quit(); return; }
    backend.ready.catch(error => publish({ event: 'service_error', error: error.message }));
    creationPreview = new CreationPreview({entry,plugins,call:(...args)=>backend.call(...args),publish});
    browserHost = new BrowserHost({ root, backend, getWindow: () => window, diagnostic: (event, details) => {
      void fs.mkdir(path.join(dataDir, 'logs'), {recursive:true})
        .then(() => fs.appendFile(path.join(dataDir, 'logs', 'desktop-browser.log'), `${new Date().toISOString()} ${event} ${JSON.stringify(details)}\n`))
        .catch(error => console.error('browser_diagnostic_write_failed', error.message));
    } });
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
      if (['file.open', 'file.save_copy', 'workspace.create', 'file.render_result', 'file.preview', 'reviews.open'].includes(method)) return fileHost.register(await backend.call(method, params));
      if (method === 'library.read_result' || (method === 'library.read' && params.source === 'local')) return fileHost.register(await backend.call(method, params));
      if (method === 'library.open_folder' && params.source === 'local') {
        const item = await backend.call('library.folder', params);
        const failure = await shell.openPath(item.path);
        if (failure) throw new Error(failure);
        return {opened:true};
      }
      if (method === 'library.external' && params.source === 'local') {
        const item = await backend.call('library.reference', params);
        const failure = await shell.openPath(item.path);
        if (failure) throw new Error(failure);
        return {opened:true};
      }
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
      if (method === 'snippet.create') return snippetHost.create(event.sender,params);
      if (method === 'snippet.start') return snippetHost.start(event.sender,params.token);
      if (method === 'snippet.event') return snippetHost.event(event.sender,params);
      if (method === 'snippet.release') return snippetHost.release(event.sender,params.token);
      if (allowed.has(method)) return backend.call(method, params);
      if (['browser.open', 'browser.navigate', 'browser.close', 'browser.command'].includes(method)) return backend.call(method, params);
      if (method === 'browser.surface') return browserHost.show(params);
      if (method === 'clipboard.read') return clipboard.readText();
      if (method === 'clipboard.write') { await clipboard.writeText(String(params.text || '')); return { ok: true }; }
      if (method === 'dialog.choose') {
        const filters = params.kind === 'skill' ? [{ name: '技能包', extensions: ['zip'] }]
          : params.kind === 'theme' ? [{ name: '主题', extensions: ['cowork-theme', 'json'] }] : [];
        const result = await dialog.showOpenDialog(window, { title: '选择文件', filters, properties: params.kind === 'files' ? ['openFile','multiSelections'] : ['openFile'] });
        return params.kind === 'files' ? (result.canceled ? [] : result.filePaths) : result.canceled ? null : result.filePaths[0];
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
      if (method === 'plugins.inventory') return {...await plugins.inventory(), modes:await backend.call('modes.list',{})};
      if (method === 'creation.prepare') {
        const session = await backend.call('creation.open', params);
        const reference = path.join(session.workspace, 'reference');
        await fs.mkdir(reference, {recursive:true});
        // Seed SDK references without overwriting edited examples or candidates.
        await fs.cp(path.join(__dirname,'../sdk'), path.join(reference,'sdk'), {recursive:true,force:false,errorOnExist:false});
        await fs.cp(path.join(__dirname,'../examples/workbench'), path.join(reference,'workbench'), {recursive:true,force:false,errorOnExist:false});
        console.info('creation_prepared', {conversation_id:session.conversation_id});
        return session;
      }
      if (method === 'creation.preview_state') return creationPreview.state(params.conversation_id);
      if (method === 'creation.preview_use') return servePlugin(creationPreview.use(params.conversation_id,params.hash));
      if (method === 'creation.preview_clear') return creationPreview.clear(params.conversation_id,params.hash);
      if (method === 'creation.preview') {
        const candidate = await backend.call('creation.candidate',params);
        await creationPreview.run(params.conversation_id,candidate.path,undefined,new AbortController().signal);
        return creationPreview.state(params.conversation_id);
      }
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
      if (method === 'app.menu_state') {
        Menu.getApplicationMenu().getMenuItemById('export-chat').enabled = Boolean(params.canExport);
        return {accepted:true};
      }
      if (method === 'app.menu') {
        if (!['file','edit','view','help'].includes(params.menu)) throw Error('菜单不存在。');
        const submenu = Menu.getApplicationMenu().getMenuItemById(params.menu).submenu;
        const zoom = window.webContents.getZoomFactor();
        const bounds = window.getContentBounds();
        const x = Math.max(0,Math.min(bounds.width,Math.round(Number(params.x || 0)*zoom)));
        const y = Math.max(0,Math.min(bounds.height,Math.round(Number(params.y || 0)*zoom)));
        return new Promise(resolve => submenu.popup({window,x,y,callback:()=>resolve({closed:true})}));
      }
      if (method === 'app.export_conversation') {
        const value = await backend.call('conversation.export', {conversation_id:params.conversation_id});
        const target = await dialog.showSaveDialog(window,{title:'导出当前聊天',defaultPath:value.name,filters:[{name:'聊天记录',extensions:['txt']}]});
        if (target.canceled) return {cancelled:true};
        await fs.writeFile(target.filePath,value.text,'utf8');
        console.info('conversation_exported',{conversation_id:params.conversation_id});
        return {saved:true};
      }
      if (method === 'app.titlebar') {
        if (process.platform === 'win32' && /^#[0-9a-f]{6}$/i.test(params.background) && /^#[0-9a-f]{6}$/i.test(params.foreground)) window.setTitleBarOverlay({color:params.background,symbolColor:params.foreground,height:40});
        return {accepted:true};
      }
      if (method === 'app.reconnect') { backend.ready = backend.start(); await backend.ready; await startup.flush(); publish({ event: 'service_reconnected' }); return { connected: true }; }
      if (method === 'app.ready') {
        startup.ready=true;
        const result=await startup.flush();
        if (!result.acknowledged) return backend.call('updates.acknowledge',{host_version:app.getVersion()});
        return result;
      }
      if (method === 'app.install_update') {
        if (installPending) throw new Error('正在准备安装，请稍候。');
        installPending=true;
        try { return await prepareInstall(backend,process.pid); }
        finally { installPending=false; }
      }
      if (method === 'app.commit_update') return commitInstall(backend,params,dataDir,quit);
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
