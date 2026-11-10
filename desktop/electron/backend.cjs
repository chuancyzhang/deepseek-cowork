const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { mkdirSync, existsSync, readFileSync, openSync, closeSync } = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const PROTOCOL = 1;

class Backend {
  constructor({ root, dataDir, resources, publish }) {
    this.pending = new Map(); this.publish = publish; this.dataDir = path.resolve(dataDir);
    this.root = root; this.resources = resources; this.detached = false;
    this.ready = this.start();
  }
  descriptor() {
    let value;
    try { value = JSON.parse(readFileSync(path.join(this.dataDir, 'desktop-service.json'), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return null; throw new Error('后台连接信息损坏，请查看诊断日志。'); }
    if (value.protocol !== PROTOCOL || path.resolve(value.data_dir || '') !== this.dataDir ||
        !Number.isInteger(value.port) || value.port < 1 || value.port > 65535 || typeof value.token !== 'string') {
      throw new Error('后台版本或数据目录不匹配，请结束旧版后台后重新打开应用。');
    }
    return value;
  }
  launch() {
    mkdirSync(path.join(this.dataDir, 'logs'), { recursive: true });
    const bundled = path.join(this.resources || '', 'service', process.platform === 'win32' ? 'cowork-service.exe' : 'cowork-service');
    const python = process.env.COWORK_PYTHON || path.join(this.root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    const executable = this.resources ? bundled : python;
    if (!existsSync(executable)) throw new Error('本地服务尚未准备好。请运行 scripts/run_desktop.py，或使用完整的 Windows 发行包。');
    const args = this.resources ? [] : ['-X', 'utf8', '-B', '-m', 'core.desktop_service'];
    args.push('--data-dir', this.dataDir, '--serve');
    const log = openSync(path.join(this.dataDir, 'logs', 'desktop-service.log'), 'a');
    // Windows detached mode requests a new console; the venv launcher may make
    // it visible again even when windowsHide hides the first process. Windows
    // processes already survive their parent with independent log handles.
    const child = spawn(executable, args, { cwd: this.root, detached: process.platform !== 'win32', windowsHide: true, stdio: ['ignore', log, log],
      env: { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1', COWORK_APP_DATA_DIR: this.dataDir,
        ...(this.resources ? { COWORK_DESKTOP_INSTALL_DIR: path.dirname(this.resources) } : {}) } });
    closeSync(log);
    child.on('error', error => { this.launchError = error; });
    child.on('exit', code => { if (code) this.launchError = new Error(`后台启动失败（${code}），请查看诊断日志。`); });
    child.unref();
  }
  async start() {
    const existing = this.descriptor();
    if (existing) {
      try { return await this.connect(existing); }
      catch (error) { if (!['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT'].includes(error.code)) throw error; }
    }
    this.launch();
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      if (this.launchError) throw this.launchError;
      const descriptor = this.descriptor();
      if (descriptor) {
        try { return await this.connect(descriptor); }
        catch (error) { if (!['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT'].includes(error.code)) throw error; }
      }
      await delay(150);
    }
    throw new Error('本地服务启动超时，请查看诊断日志。');
  }
  connect(descriptor) {
    return new Promise((resolve, reject) => {
      const socket = net.createConnection({ host: '127.0.0.1', port: descriptor.port });
      let authenticated = false;
      const timer = setTimeout(() => { const error = new Error('后台连接超时。'); error.code = 'ETIMEDOUT'; socket.destroy(error); }, 10000);
      socket.on('connect', () => socket.write(JSON.stringify({ token: descriptor.token, protocol: PROTOCOL }) + '\n'));
      const reader = createInterface({ input: socket });
      // Readline forwards input-stream errors as its own error event. Handling
      // only the Socket error still crashes Electron during a reset handshake.
      reader.on('error', error => socket.destroy(error));
      reader.on('line', line => {
        let value;
        try { value = JSON.parse(line); } catch { socket.destroy(new Error('后台返回了无效数据。')); return; }
        if (!authenticated) {
          if (value.event !== 'ready' || value.protocol !== PROTOCOL || value.instance !== descriptor.instance) {
            socket.destroy(new Error('后台连接校验失败，请查看诊断日志。')); return;
          }
          authenticated = true; clearTimeout(timer); this.socket = socket; resolve(value); return;
        }
        if (value.id) {
          const pending = this.pending.get(value.id);
          if (!pending) return;
          clearTimeout(pending.timer); this.pending.delete(value.id);
          value.error ? pending.reject(new Error(value.error.message)) : pending.resolve(value.result);
        } else this.publish(value);
      });
      socket.on('error', error => { clearTimeout(timer); if (!authenticated) reject(error); });
      socket.on('close', () => {
        clearTimeout(timer);
        if (!authenticated) { const error = new Error('后台连接已关闭。'); error.code = 'ECONNRESET'; reject(error); return; }
        if (this.socket !== socket) return;
        this.socket = null;
        const error = new Error('后台连接中断，操作结果需要重新核对；输入和已保存内容仍保留。');
        for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
        this.pending.clear();
        if (!this.detached) {
          this.ready = this.reconnect();
          this.ready.catch(error => this.publish({ event: 'service_error', error: error.message }));
        }
      });
    });
  }
  async reconnect() {
    for (let attempt = 1; attempt <= 5 && !this.detached; attempt++) {
      this.publish({ event: 'service_reconnecting', attempt, max_attempts: 5 });
      await delay(250 * 2 ** (attempt - 1));
      try {
        const descriptor = this.descriptor();
        if (!descriptor) continue;
        const ready = await this.connect(descriptor);
        this.publish({ event: 'service_reconnected' });
        return ready;
      } catch { /* Only reconnect the transport; never resubmit commands. */ }
    }
    throw new Error('暂时无法连接后台。已保存内容仍在本机，可重新连接或打开诊断日志。');
  }
  async call(method, params = {}) {
    await this.ready;
    if (!this.socket?.writable) throw new Error('本地服务连接已关闭。');
    const id = randomUUID();
    const frame = JSON.stringify({ id, method, params }) + '\n';
    if (Buffer.byteLength(frame, 'utf8') > 64 * 1024 * 1024) throw new Error('本次内容超过 64 MiB 传输上限，尚未提交；当前编辑仍保留。');
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('操作确认超时，请检查状态后再试。输入内容已保留。')); }, 60000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.write(frame, error => {
        if (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
      });
    });
  }
  detach() { this.detached = true; this.socket?.end(); }
  async stop() { await this.call('app.shutdown'); this.detach(); }
}
module.exports = { Backend };
