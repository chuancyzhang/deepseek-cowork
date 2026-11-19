const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {atomicWrite} = require('./platform.cjs');

const validId = /^[a-z][a-z0-9.-]{1,80}$/;
const kinds = new Set(['workbench','page','panel','editor','command','menu','toolbar']);
const capabilities = new Set(['document.read','document.edit','conversations.read','conversations.write','runs.control',
  'drafts.read','drafts.write','files.read','files.write','management.read','management.write','navigation','theme','dialogs','desktop']);
const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8',
  '.css':'text/css; charset=utf-8','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg',
  '.jpeg':'image/jpeg','.webp':'image/webp','.gif':'image/gif','.woff':'font/woff','.woff2':'font/woff2','.ttf':'font/ttf'};

class PluginHost {
  constructor(dataDir, bundled) {
    this.root = path.join(dataDir,'plugins'); this.bundled = bundled;
    this.previews = new Map(); this.packages = new Map(); this.serial = Promise.resolve();
    this.previewPackages = new Map();
  }
  async inspect(directory, {cache=true, stayInDirectory=false} = {}) {
    const real = await fs.realpath(directory), files = new Map(); let total = 0;
    if (stayInDirectory && path.relative(directory,real)) throw new Error('候选界面必须位于设计任务的 interface 目录。');
    const walk = async relative => {
      for (const entry of (await fs.readdir(path.join(real,relative),{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))) {
        const name = relative ? `${relative}/${entry.name}` : entry.name;
        if (entry.isSymbolicLink() || name.includes('\\') || name.includes(':')) throw new Error('插件包不能包含符号链接或无效资源路径。');
        if (entry.isDirectory()) { await walk(name); continue; }
        if (!entry.isFile() || !mime[path.extname(name).toLowerCase()]) throw new Error('插件包包含不支持的资源：'+name);
        const stat = await fs.stat(path.join(real,name)); total += stat.size;
        if (stat.size > 4*1024*1024 || total > 16*1024*1024 || files.size >= 512) throw new Error('插件资源过大：单文件最多 4 MiB，完整包最多 16 MiB、512 个文件。');
        files.set(name,await fs.readFile(path.join(real,name)));
      }
    };
    await walk('');
    const manifest = JSON.parse(files.get('manifest.json')?.toString('utf8') || '{}');
    if (!validId.test(manifest.id) || ![1,2].includes(manifest.hostApi) || !/^\d+\.\d+\.\d+$/.test(manifest.version)) throw new Error('插件标识、版本或宿主接口版本无效。');
    const toolbarOnly = manifest.hostApi === 2 && Array.isArray(manifest.contributes) &&
      manifest.contributes.length > 0 && manifest.contributes.every(item => item?.kind === 'toolbar');
    if (typeof manifest.name !== 'string' || !manifest.name || manifest.name.length > 80 ||
        (!toolbarOnly || manifest.entry !== undefined) && (!files.has(manifest.entry) || !manifest.entry.endsWith('.html'))) throw new Error('插件需要名称和包内 HTML 入口。');
    const checks = manifest.previewChecks;
    if (checks !== undefined && (!Array.isArray(checks) || checks.length > 5 || checks.some(check =>
      !check || !['button','text'].every(key => typeof check[key] === 'string' && check[key].trim() && check[key].length <= 100)))) {
      throw new Error('previewChecks 最多包含 5 项，每项需要按钮名 button 和预期可见文字 text。');
    }
    const caps = manifest.capabilities || [];
    if (!Array.isArray(caps) || caps.some(cap=>!capabilities.has(cap))) throw new Error('插件请求了当前版本不支持的能力。');
    if (manifest.hostApi === 1) {
      if (manifest.id !== 'cowork.writing' || caps.some(cap=>!['document.read','document.edit'].includes(cap))) throw new Error('hostApi 1 仅适配原写作编辑器，请升级至 hostApi 2。');
      manifest.contributes = [{id:'writing',kind:'editor',target:'markdown'}];
    } else {
      if (!Number.isInteger(manifest.stateVersion) || manifest.stateVersion < 1 || !Array.isArray(manifest.contributes) || !manifest.contributes.length) throw new Error('hostApi 2 需要状态版本和扩展点声明。');
      const ids = new Set();
      let toolbar = false;
      for (const item of manifest.contributes) {
        if (!validId.test(item.id) || ids.has(item.id) || !kinds.has(item.kind) || typeof item.target !== 'string' || !/^[a-zA-Z0-9.*_-]{1,80}$/.test(item.target)) throw new Error('插件扩展点声明无效或重复。');
        if (item.entry && (!files.has(item.entry) || !item.entry.endsWith('.html'))) throw new Error('扩展点入口不存在。');
        if (['command','menu'].includes(item.kind) && (!item.command || !validId.test(item.command) || typeof item.title !== 'string')) throw new Error('命令与菜单需要稳定标识和显示名称。');
        if (item.kind === 'panel' && item.mode && !['replace','append'].includes(item.mode)) throw new Error('面板选择方式无效。');
        if (item.kind === 'toolbar') {
          if (toolbar || !toolbarOnly || item.target !== 'chat' || item.action !== 'usage' || item.entry !== undefined ||
              item.showRate !== undefined && typeof item.showRate !== 'boolean') throw new Error('顶部工具栏插件只支持一个 chat 用量统计按钮及可选的 showRate。');
          toolbar = true;
        }
        ids.add(item.id);
      }
    }
    const digest = crypto.createHash('sha256');
    for (const [name,bytes] of [...files].sort(([a],[b])=>a.localeCompare(b))) digest.update(name+'\0'+bytes.length+'\0').update(bytes);
    const plugin = {manifest,files,hash:digest.digest('hex'),source:real};
    if (cache) this.packages.set(plugin.hash,plugin);
    return plugin;
  }
  retainPreview(snapshot) {
    const entry = this.previewPackages.get(snapshot.hash) || {snapshot, references:0};
    entry.references++;
    this.previewPackages.set(snapshot.hash,entry);
    return () => {
      if (--entry.references === 0) this.previewPackages.delete(snapshot.hash);
    };
  }
  async bundledPlugins() {
    const list = [await this.inspect(this.bundled)], workbench = path.join(path.dirname(this.bundled),'workbench');
    try { list.push(await this.inspect(workbench)); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    return list;
  }
  async state() {
    try { const value = JSON.parse(await fs.readFile(path.join(this.root,'state.json'),'utf8')); return value.schemaVersion === 2 ? value : {schemaVersion:2,versions:value,selected:{}}; }
    catch (error) { if (error.code === 'ENOENT') return {schemaVersion:2,versions:{},selected:{}}; throw error; }
  }
  async writeState(state) { await fs.mkdir(this.root,{recursive:true}); await atomicWrite(path.join(this.root,'state.json'),JSON.stringify(state,null,2)); }
  describe(plugin,extra={}) {
    const entry = plugin.files.get(plugin.manifest.entry);
    return {...plugin.manifest,hash:plugin.hash,html:entry && this.document(entry.toString('utf8')),...extra};
  }
  async list() {
    const state = await this.state(), list = (await this.bundledPlugins()).map(plugin=>({...plugin.manifest,hash:plugin.hash,builtin:true,enabled:true}));
    for (const [id,item] of Object.entries(state.versions)) {
      if (!validId.test(id) || !/^[a-f0-9]{64}$/.test(item.active)) throw new Error('插件索引损坏。请通过恢复入口停用自定义界面。');
      const plugin = await this.inspect(path.join(this.root,id,item.active));
      if (plugin.hash !== item.active || plugin.manifest.id !== id) throw new Error('插件包已在安装后改变，请重新预览并应用。');
      const record = {...plugin.manifest,hash:plugin.hash,builtin:false,previous:Boolean(item.previous),enabled:item.enabled!==false};
      const index = list.findIndex(p=>p.id===id); index>=0 ? list.splice(index,1,record) : list.push(record);
    }
    return list;
  }
  async inventory() {
    const state = await this.state(), listed = await this.list(), selected = {};
    const bundled=(await this.bundledPlugins()).map(plugin=>({...plugin.manifest,hash:plugin.hash,builtin:true,enabled:true}));
    const plugins=listed.map(plugin=>plugin.enabled===false && bundled.some(item=>item.id===plugin.id) ? bundled.find(item=>item.id===plugin.id) : plugin);
    for (const plugin of bundled) for (const extension of plugin.contributes) {
      if (!['command','menu'].includes(extension.kind)) selected[`${extension.kind}:${extension.target}`]=[`${plugin.id}/${extension.id}`];
    }
    for (const [slot,references] of Object.entries(state.selected)) {
      const valid=references.filter(reference=>plugins.some(plugin=>plugin.enabled && plugin.contributes.some(extension=>`${plugin.id}/${extension.id}`===reference)));
      if (valid.length) selected[slot]=valid;
    }
    // Keep explicit custom text selections for code unless the user has chosen
    // a dedicated code editor. Only the bundled writing default is narrowed.
    const legacy = state.selected['editor:text'];
    if (!state.selected['editor:code']?.length && legacy?.some(ref => ref !== 'cowork.writing/writing' || plugins.some(plugin=>plugin.id==='cowork.writing'&&!plugin.builtin))) selected['editor:code'] = selected['editor:text'];
    this.checkCommands(plugins); return {plugins,selected,catalog:listed};
  }
  checkCommands(plugins) {
    const commands = new Map();
    for (const plugin of plugins.filter(p=>p.enabled)) for (const item of plugin.contributes.filter(item=>item.kind==='command')) {
      if (commands.has(item.command)) throw new Error(`命令标识冲突：${item.command}（${commands.get(item.command)} / ${plugin.name}）`);
      commands.set(item.command,plugin.name);
    }
  }
  checkPlacement(plugin, inventory) {
    if (plugin.contributes.some(extension => extension.kind === 'toolbar') &&
        inventory.selected['workbench:main']?.some(ref =>
          !inventory.plugins.some(item => item.builtin && item.contributes.some(extension => [item.id,extension.id].join('/') === ref)))) {
      throw new Error('当前完整工作台插件使用自己的布局。请在该工作台插件中直接修改工具栏。');
    }
  }
  async preview(directory) {
    return this.previewSnapshot((await this.inspect(directory)).hash);
  }
  previewSnapshot(hash) {
    if (this.previews.size>=8) throw new Error('请先关闭已有预览，再添加插件。');
    const plugin=this.packages.get(hash) || this.previewPackages.get(hash)?.snapshot;
    if (!plugin) throw new Error('预览版本已失效，请重新试跑。');
    this.packages.set(hash,plugin);
    const token=crypto.randomUUID(); this.previews.set(token,plugin); return this.describe(plugin,{token});
  }
  async stage(token) {
    const plugin=this.previews.get(token); if (!plugin) throw new Error('预览已失效，请重新选择插件。');
    const directory=path.join(this.root,plugin.manifest.id,plugin.hash); await fs.mkdir(directory,{recursive:true});
    for (const [name,bytes] of plugin.files) { const file=path.join(directory,name); await fs.mkdir(path.dirname(file),{recursive:true}); await atomicWrite(file,bytes); }
    return this.describe(plugin,{token});
  }
  async install(token) {
    const operation=this.serial.catch(()=>undefined).then(async()=>{
      const plugin=this.previews.get(token); if (!plugin) throw new Error('预览已失效，请重新选择插件。');
      await this.stage(token);
      const state=await this.state(),id=plugin.manifest.id,old=state.versions[id]?.active,inventory=await this.inventory();
      const defaults=inventory.selected;
      this.checkPlacement(plugin.manifest, inventory);
      this.checkCommands([...(await this.list()).filter(item=>item.id!==id),{...plugin.manifest,enabled:true}]);
      state.versions[id]={active:plugin.hash,previous:old!==plugin.hash ? old||null : state.versions[id]?.previous||null,enabled:true};
      for (const item of plugin.manifest.contributes) {
        if (['command','menu'].includes(item.kind)) continue;
        const slot=`${item.kind}:${item.target}`,reference=`${id}/${item.id}`;
        state.selected[slot]=item.kind==='panel' && item.mode==='append' ? [...(state.selected[slot]||defaults[slot]||[]).filter(ref=>ref!==reference),reference] : [reference];
      }
      await this.writeState(state); this.previews.delete(token); return this.list();
    });
    this.serial=operation; return operation;
  }
  discard(token) { this.previews.delete(token); }
  async rollbackPreview(id) {
    const state=await this.state(),previous=state.versions[id]?.previous;
    if (!validId.test(id) || !previous || !/^[a-f0-9]{64}$/.test(previous)) throw new Error('没有上一版可供预览。');
    return this.preview(path.join(this.root,id,previous));
  }
  async enablePreview(id) {
    const state=await this.state(),active=state.versions[id]?.active;
    if (!validId.test(id) || !active || !/^[a-f0-9]{64}$/.test(active)) throw new Error('没有已安装的插件版本。');
    return this.preview(path.join(this.root,id,active));
  }
  async rollback(id) {
    const state=await this.state(); if (!validId.test(id) || !state.versions[id]) throw new Error('没有可恢复的插件版本。');
    if (state.versions[id].previous) return this.install((await this.rollbackPreview(id)).token);
    delete state.versions[id]; for (const [slot,refs] of Object.entries(state.selected)) state.selected[slot]=refs.filter(ref=>!ref.startsWith(id+'/'));
    await this.writeState(state); return this.list();
  }
  async recover() { await this.writeState({schemaVersion:2,versions:{},selected:{}}); return this.inventory(); }
  async disable(id) { const state=await this.state(); if (!state.versions[id]) throw new Error('内置界面不能停用。'); state.versions[id].enabled=false; await this.writeState(state); return this.inventory(); }
  async load(id) {
    if (!validId.test(id)) throw new Error('无效插件。');
    const state=await this.state(),item=state.versions[id];
    const plugin=item && item.enabled!==false ? await this.inspect(path.join(this.root,id,item.active)) : (await this.bundledPlugins()).find(p=>p.manifest.id===id);
    if (!plugin || plugin.manifest.id!==id) throw new Error('插件不存在或已停用。');
    return this.describe(plugin,{builtin:!item || item.enabled===false});
  }
  async export(id,destination) {
    const loaded=await this.load(id),plugin=this.packages.get(loaded.hash),target=path.join(destination,`${id}-${Date.now()}`); await fs.mkdir(target,{recursive:false});
    for (const [name,bytes] of plugin.files) { const file=path.join(target,name); await fs.mkdir(path.dirname(file),{recursive:true}); await fs.writeFile(file,bytes); }
    return {path:target};
  }
  asset(hash,name) {
    const plugin=this.packages.get(hash) || this.previewPackages.get(hash)?.snapshot;
    const bytes=plugin?.files.get(name); if (!bytes) return null;
    return {body:name.endsWith('.html') ? this.document(bytes.toString('utf8')) : bytes,type:mime[path.extname(name).toLowerCase()]};
  }
  document(html) { return `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; form-action 'none'; base-uri 'none'">`+html; }
}
const PLUGIN_SCHEME={scheme:'cowork-plugin',privileges:{standard:true,secure:true,supportFetchAPI:true,corsEnabled:true}};
module.exports={PluginHost,PLUGIN_SCHEME};
