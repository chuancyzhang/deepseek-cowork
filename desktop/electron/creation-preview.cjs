const {BrowserWindow, nativeTheme} = require('electron');
const {setTimeout:delay} = require('node:timers/promises');

function query(frame, operation, argument = null) {
  return frame.executeJavaScript('('+operation.toString()+')('+JSON.stringify(argument)+')');
}

function clickButton(name) {
  const buttons = [...document.querySelectorAll('button,[role="button"]')].filter(button =>
    (button.getAttribute('aria-label') || button.innerText || button.getAttribute('title'))?.trim() === name &&
    button.getClientRects().length && button.checkVisibility() && !button.disabled &&
    button.getAttribute('aria-disabled') !== 'true');
  if (buttons.length !== 1) return buttons.length;
  buttons[0].click();
  return 1;
}

function readSurface() {
  return {text:document.body.innerText.trim().slice(0, 3000),
    overflow:document.documentElement.scrollWidth > innerWidth + 1};
}

async function until(probe, signal, message, timeout = 1800) {
  const deadline = Date.now() + timeout;
  do {
    signal.throwIfAborted();
    const result = await probe();
    if (result) return result;
    await delay(60, undefined, {signal});
  } while (Date.now() < deadline);
  throw new Error(message);
}

async function trial(entry, plugin, theme, mode, signal) {
  const win = new BrowserWindow({show:false, width:940, height:680, webPreferences:{
    offscreen:true, backgroundThrottling:false, contextIsolation:true, sandbox:true, nodeIntegration:false,
  }});
  const wc = win.webContents, url = entry+'#creation-preview';
  const destroy = () => { if (!win.isDestroyed()) win.destroy(); };
  const diagnostics = [];
  signal.addEventListener('abort', destroy, {once:true});
  wc.setWindowOpenHandler(() => ({action:'deny'}));
  wc.on('will-frame-navigate', event => {
    if (event.url !== url && !event.url.startsWith('cowork-plugin://'+plugin.hash+'/')) event.preventDefault();
  });
  wc.on('console-message', info => {
    if (info.level !== 'error' || diagnostics.length >= 6) return;
    diagnostics.push(String(info.message).slice(0, 350));
  });
  const checks = [];
  const hash = plugin.hash;
  let phase = 'mount';
  try {
    signal.throwIfAborted();
    await win.loadURL(url);
    await until(() => query(wc, () => typeof window.renderCreationPreview === 'function'), signal, '候选试跑页面未打开。');
    await query(wc, ({plugin, theme, mode}) => {
      window.renderCreationPreview(plugin, theme, mode);
    }, {plugin,theme,mode});
    await until(async () => {
      const value = await query(wc, expectedHash => {
        const root = document.querySelector('[data-preview-hash]');
        return {mounted:root?.dataset.previewHash === expectedHash, ready:root && !root.querySelector('.plugin-surface[data-ready="false"]'),
          failure:root?.querySelector('.plugin-failure p')?.textContent};
      },hash);
      if (value.failure || diagnostics.length) throw new Error(value.failure || diagnostics[0]);
      return value.mounted && value.ready;
    }, signal, '候选界面未就绪，请检查入口脚本和 SDK ready 调用。', 10000);

    const first = plugin.contributes.find(item => item.kind !== 'menu') || plugin.contributes[0];
    let frame = wc.mainFrame;
    if (first.kind !== 'toolbar') {
      const target = 'cowork-plugin://'+plugin.hash+'/'+(first.entry || plugin.entry);
      frame = await until(() => wc.mainFrame.framesInSubtree.find(item => item.url === target), signal, '未找到候选界面的渲染框。');
    }
    const initial = await query(frame,readSurface);
    if (initial.overflow) throw new Error('候选界面产生了横向溢出。');
    checks.push('候选已加载');
    const toolbar = first.kind === 'toolbar';
    if (toolbar && first.showRate) {
      phase = 'simulation';
      if (await query(frame,clickButton,'模拟生成') !== 1) throw new Error('无法开始模拟生成。');
      const rate = () => query(frame, () => document.querySelector('.usage-toolbar-rate')?.textContent || '');
      const before = await until(rate,signal,'模拟生成时工具栏未显示 tok/s。',3200);
      await until(async () => { const after = await rate();return after && after !== before; }, signal,'工具栏的 tok/s 没有随输出更新。',2200);
      checks.push('模拟输出时工具栏的 tok/s 随输出更新');
    }
    const declared = plugin.previewChecks || [];
    const actions = toolbar ? [{button:'用量统计',text:'生成速度'}, ...declared.filter(check => check.button !== '用量统计')] : declared;
    phase = 'interaction';
    for (const {button,text} of actions) {
      signal.throwIfAborted();
      const shown = () => query(frame,expected => document.body.innerText.includes(expected),text);
      if (await shown()) throw new Error('点击「'+button+'」前「'+text+'」已可见，请指定能验证点击结果的文字。');
      const count = await query(frame,clickButton,button);
      if (count !== 1) throw new Error('需要一个可点击的「'+button+'」按钮，实际找到 '+count+' 个。');
      await until(shown, signal, '点击「'+button+'」后找不到「'+text+'」。');
      checks.push('点击「'+button+'」后显示「'+text+'」');
    }
    if (diagnostics.length) throw new Error(diagnostics[0]);
    const surface = await query(frame,readSurface);
    phase = 'image';
    const screenshot = await wc.capturePage(undefined,{stayHidden:true});
    if (screenshot.isEmpty()) throw new Error('候选画面截图为空，请重新试跑。');
    return {ok:true,hash,checks,text:surface.text.slice(0,800),screenshot:screenshot.toPNG().toString('base64')};
  } catch (error) {
    signal.throwIfAborted();
    const result = {ok:false,hash,checks,phase,error:error.message};
    if (!wc.isDestroyed()) {
      const screenshot = await wc.capturePage(undefined,{stayHidden:true}).catch(() => null);
      if (screenshot && !screenshot.isEmpty()) result.screenshot = screenshot.toPNG().toString('base64');
    }
    return result;
  } finally {
    signal.removeEventListener('abort',destroy);
    destroy();
  }
}

class CreationPreview {
  constructor({entry, plugins, call, publish}) {
    Object.assign(this,{entry, plugins, call, publish});
    this.states = new Map();
    this.requests = new Map();
    this.retained = new Map();
  }
  state(cid) {
    return this.states.get(cid) || {revision:0,plugin:null,checking:false,error:''};
  }
  set(cid, value) {
    const state = {...value,revision:this.state(cid).revision+1};
    this.states.set(cid,state);
    this.publish({event:'creation_preview_changed',conversation_id:cid,data:state});
  }
  async run(cid, directory, runId, abort) {
    const previous = this.state(cid);
    if (previous.checking) return {ok:false,error:'已有一个版本正在试跑，请稍候再提交。'};
    this.set(cid,{...previous,checking:true,error:''});
    const signal = AbortSignal.any([AbortSignal.timeout(20000),abort]);
    console.info('creation_preview_started',{conversation_id:cid,run_id:runId});
    let plugin, result, release;
    let phase = 'package';
    try {
      if (!directory) throw new Error('尚未生成界面候选。');
      const [snapshot, inventory, appearance] = await Promise.all([
        this.plugins.inspect(directory,{cache:false,stayInDirectory:true}),this.plugins.inventory(),this.call('themes.list', {conversation_id:cid}),
      ]);
      signal.throwIfAborted();
      plugin = {...snapshot.manifest,hash:snapshot.hash};
      this.plugins.checkPlacement(plugin,inventory);
      phase = 'appearance';
      const dark = appearance.appearance.mode === 'dark' || appearance.appearance.mode === 'system' && nativeTheme.shouldUseDarkColors;
      const theme = await this.call('themes.resolve',{dark});
      signal.throwIfAborted();
      release = this.plugins.retainPreview(snapshot);
      phase = 'render';
      result = await trial(this.entry,plugin,theme,dark?'dark':'light',signal);
      signal.throwIfAborted();
    } catch (error) {
      result = {ok:false,hash:plugin?.hash,phase,error:abort.aborted ? '试跑已停止。' :
        signal.aborted ? '候选试跑超时，已保留此前的预览。' : error.message};
    }
    if (result.ok) {
      const releasePrevious = this.retained.get(cid);
      this.retained.set(cid,release);
      this.set(cid,{plugin,checking:false,error:''});
      releasePrevious?.();
    } else {
      release?.();
      this.set(cid,{...previous,checking:false,error:result.error});
    }
    const details = {conversation_id:cid,run_id:runId,hash:result.hash,ok:result.ok,phase:result.phase};
    if (result.ok) console.info('creation_preview_completed',details);
    else console.warn('creation_preview_failed',details);
    return result;
  }
  async handle(event) {
    const {conversation_id:cid,run_id:runId,data:{id,path}}=event;
    const controller = new AbortController();
    this.requests.set(id,controller);
    try {
      const result = await this.run(cid,path,runId,controller.signal);
      await this.call('creation.preview_reply',{id,conversation_id:cid,run_id:runId,result});
    } finally { this.requests.delete(id); }
  }
  cancel(id) { this.requests.get(id)?.abort(); }
  use(cid, hash) {
    const state = this.state(cid);
    if (state.checking || !state.plugin || state.plugin.hash !== hash) throw new Error('候选已变化，请核对当前预览后再应用。');
    return this.plugins.previewSnapshot(hash);
  }
  clear(cid, hash) {
    const state = this.state(cid);
    if (!state.checking && state.plugin?.hash === hash) {
      this.set(cid,{plugin:null,checking:false,error:''});
      this.retained.get(cid)?.();
      this.retained.delete(cid);
    }
  }
}

module.exports = {CreationPreview};
