const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

const validId = /^[a-z][a-z0-9.-]{1,80}$/;
class PluginHost {
  constructor(dataDir, bundled) { this.root = path.join(dataDir, 'plugins'); this.bundled = bundled; this.previews = new Map(); }
  async inspect(directory) {
    const real = await fs.realpath(directory);
    const manifest = JSON.parse(await fs.readFile(path.join(real, 'manifest.json'), 'utf8'));
    if (!validId.test(manifest.id) || manifest.hostApi !== 1 || !/^[0-9]+\.[0-9]+\.[0-9]+$/.test(manifest.version)) throw new Error('插件标识、版本或宿主接口版本无效。');
    if (typeof manifest.name !== 'string' || manifest.name.length > 80 || manifest.entry !== 'index.html') throw new Error('插件需要名称和 index.html 入口。');
    const caps = manifest.capabilities || [];
    if (!Array.isArray(caps) || caps.some(cap => !['document.read', 'document.edit'].includes(cap))) throw new Error('插件请求了当前版本不支持的能力。');
    const stat = await fs.lstat(path.join(real, 'index.html'));
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 2 * 1024 * 1024) throw new Error('插件页面必须是小于 2 MiB 的本地 HTML 文件。');
    const html = await fs.readFile(path.join(real, 'index.html'), 'utf8');
    const hash = crypto.createHash('sha256').update(JSON.stringify(manifest)).update(html).digest('hex');
    return { manifest, html, hash, source: real };
  }
  async state() {
    try { return JSON.parse(await fs.readFile(path.join(this.root, 'state.json'), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return {}; throw error; }
  }
  async writeState(state) {
    await fs.mkdir(this.root, { recursive: true });
    const temp = path.join(this.root, `state-${crypto.randomUUID()}.tmp`);
    await fs.writeFile(temp, JSON.stringify(state, null, 2));
    await fs.rename(temp, path.join(this.root, 'state.json'));
  }
  async list() {
    const bundled = await this.inspect(this.bundled);
    const state = await this.state();
    const list = [{ ...bundled.manifest, hash: bundled.hash, builtin: true }];
    for (const [id, item] of Object.entries(state)) {
      if (!validId.test(id)) throw new Error('插件索引损坏。');
      const plugin = await this.inspect(path.join(this.root, id, item.active));
      const record = { ...plugin.manifest, hash: plugin.hash, builtin: false, previous: Boolean(item.previous) };
      const index = list.findIndex(p => p.id === id);
      index >= 0 ? list.splice(index, 1, record) : list.push(record);
    }
    return list;
  }
  async preview(directory) {
    const plugin = await this.inspect(directory);
    const token = crypto.randomUUID(); this.previews.set(token, plugin);
    return { token, ...plugin.manifest, hash: plugin.hash, html: this.document(plugin.html) };
  }
  async install(token) {
    const plugin = this.previews.get(token);
    if (!plugin) throw new Error('预览已失效，请重新选择插件。');
    const { manifest, html, hash } = plugin;
    const directory = path.join(this.root, manifest.id, hash);
    await fs.mkdir(directory, { recursive: true });
    await fs.writeFile(path.join(directory, 'manifest.json'), JSON.stringify(manifest, null, 2));
    await fs.writeFile(path.join(directory, 'index.html'), html);
    const state = await this.state();
    const old = state[manifest.id]?.active;
    state[manifest.id] = { active: hash, previous: old !== hash ? old || null : state[manifest.id]?.previous || null };
    await this.writeState(state); this.previews.delete(token);
    return this.list();
  }
  async rollback(id) {
    const state = await this.state();
    if (!validId.test(id) || !state[id]) throw new Error('没有可恢复的插件版本。');
    if (state[id].previous) state[id] = { active: state[id].previous, previous: state[id].active };
    else delete state[id];
    await this.writeState(state); return this.list();
  }
  async load(id) {
    if (!validId.test(id)) throw new Error('无效插件。');
    const state = await this.state();
    const plugin = await this.inspect(state[id] ? path.join(this.root, id, state[id].active) : this.bundled);
    if (plugin.manifest.id !== id) throw new Error('插件不存在。');
    return { ...plugin.manifest, html: this.document(plugin.html), hash: plugin.hash };
  }
  async export(id, destination) {
    const state = await this.state();
    if (!validId.test(id)) throw new Error('无效插件。');
    const plugin = await this.inspect(state[id] ? path.join(this.root, id, state[id].active) : this.bundled);
    const target = path.join(destination, `${id}-${Date.now()}`);
    await fs.mkdir(target, { recursive: false });
    await fs.writeFile(path.join(target, 'manifest.json'), JSON.stringify(plugin.manifest, null, 2));
    await fs.writeFile(path.join(target, 'index.html'), plugin.html);
    return { path: target };
  }
  document(html) {
    const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'">`;
    return policy + html;
  }
}
module.exports = { PluginHost };
