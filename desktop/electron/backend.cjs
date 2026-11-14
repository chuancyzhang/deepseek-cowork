const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { mkdirSync, existsSync, readFileSync, openSync, closeSync } = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const {platform} = require('./platform.cjs');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const PROTOCOL = 2;

class Backend {
  constructor({ root, dataDir, resources, publish, startupTimeoutMs = 120000 }) {
    this.pending = new Map(); this.publish = publish; this.dataDir = path.resolve(dataDir);
    this.root = root; this.resources = resources; this.detached = false;
    this.startupTimeoutMs = startupTimeoutMs; this.initialized = false;
    this.ready = this.start();
  }
  descriptor() {
    let value;
    try { value = JSON.parse(readFileSync(path.join(this.dataDir, 'desktop-service.json'), 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return null; throw new Error('后台连接信息损坏，请查看诊断日志。'); }
    if (![1,PROTOCOL].includes(value.protocol) || path.resolve(value.data_dir || '') !== this.dataDir ||
        !Number.isInteger(value.port) || value.port < 1 || value.port > 65535 || typeof value.token !== 'string') {
      throw new Error('后台版本或数据目录不匹配，请结束旧版后台后重新打开应用。');
    }
    return value;
  }
  launch() {
    this.launchError = null;
    mkdirSync(path.join(this.dataDir, 'logs'), { recursive: true });
    const bundled = path.join(this.resources || '', 'service', platform.serviceName);
    const python = process.env.COWORK_PYTHON || path.join(this.root, '.venv', ...platform.pythonParts);
    const executable = this.resources ? bundled : python;
    if (!existsSync(executable)) throw new Error('本地服务尚未准备好。请运行 scripts/run_desktop.py，或使用完整的 Windows 发行包。');
    const args = this.resources ? [] : ['-X', 'utf8', '-B', '-m', 'core.desktop_service'];
    args.push('--data-dir', this.dataDir, '--serve');
    const log = openSync(path.join(this.dataDir, 'logs', 'desktop-service.log'), 'a');
    // Windows detached mode requests a new console; the venv launcher may make
    // it visible again even when windowsHide hides the first process. Windows
    // processes already survive their parent with independent log handles.
    const child = spawn(executable, args, { cwd: this.root, ...platform.childOptions, stdio: ['ignore', log, log],
      env: { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1', COWORK_APP_DATA_DIR: this.dataDir,
        ...(this.resources ? { COWORK_DESKTOP_INSTALL_DIR: path.dirname(this.resources) } : {}) } });
    closeSync(log);
    this.child = child;
    child.on('error', error => { this.launchError = error; });
    child.on('exit', code => { if (code) this.launchError = new Error(`后台启动失败（${code}），请查看诊断日志。`); });
    child.unref();
  }
  async start() {
    const existing = this.descriptor();
    if (existing) {
      try {
        const ready = await this.connect(existing);
        if (existing.protocol === PROTOCOL) return ready;
        this.upgrading = true;
        this.publish({event:'service_reconnecting',message:'正在保存原版任务并升级后台…'});
        await this.request('app.shutdown'); this.socket?.end();
        const deadline = Date.now()+30000;
        while (this.descriptor()?.instance === existing.instance) {
          if (Date.now()>deadline) throw new Error('原版后台尚未结束，已有内容保留；请稍后重新打开。');
          await delay(100);
        }
      }
      catch (error) { if (error.startupConnected || !['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT'].includes(error.code)) throw error; }
      finally { this.upgrading = false; }
    }
    if (this.detached) throw new Error('启动已停止。');
    this.launch();
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      if (this.detached) throw new Error('启动已停止。');
      if (this.launchError) throw this.launchError;
      const descriptor = this.descriptor();
      if (descriptor) {
        try { return await this.connect(descriptor); }
        catch (error) { if (error.startupConnected || !['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT'].includes(error.code)) throw error; }
      }
      await delay(150);
    }
    throw new Error('本地服务启动超时，请查看诊断日志。');
  }
  connect(descriptor, { waitForReady = true } = {}) {
    return new Promise((resolve, reject) => {
      const socket = net.createConnection({ host: '127.0.0.1', port: descriptor.port });
      let authenticated = false, initialized = false;
      let timer = setTimeout(() => { const error = new Error('后台连接超时。'); error.code = 'ETIMEDOUT'; socket.destroy(error); }, 10000);
      socket.on('connect', () => socket.write(JSON.stringify({ token: descriptor.token, protocol: descriptor.protocol }) + '\n'));
      const reader = createInterface({ input: socket });
      // Readline forwards input-stream errors as its own error event. Handling
      // only the Socket error still crashes Electron during a reset handshake.
      reader.on('error', error => socket.destroy(error));
      reader.on('line', line => {
        let value;
        try { value = JSON.parse(line); } catch { socket.destroy(new Error('后台返回了无效数据。')); return; }
        if (!authenticated || value.event === 'starting' || value.event === 'ready') {
          if (!['ready','starting'].includes(value.event) || value.protocol !== descriptor.protocol || value.instance !== descriptor.instance) {
            socket.destroy(new Error('后台连接校验失败，请查看诊断日志。')); return;
          }
          authenticated = true; clearTimeout(timer); this.socket = socket; this.pid = descriptor.pid;
          if (value.event === 'starting') {
            this.initialized = false;
            this.startupState = {event:'service_starting',message:value.message || '正在恢复工作台…'};
            this.publish(this.startupState);
            // Only real initialization progress extends this deadline. A live but
            // stalled process must still produce a visible, bounded failure.
            timer = setTimeout(() => socket.destroy(new Error('启动进度长时间未更新，请查看诊断日志后重试。')),
              this.startupTimeoutMs || 120000);
            if (!waitForReady) resolve(value);
            return;
          }
          initialized = true; this.initialized = true; this.startupState = null; resolve(value); return;
        }
        if (value.id) {
          const pending = this.pending.get(value.id);
          if (!pending) return;
          clearTimeout(pending.timer); this.pending.delete(value.id);
          value.error ? pending.reject(new Error(value.error.message)) : pending.resolve(value.result);
        } else this.publish(value);
      });
      socket.on('error', error => { clearTimeout(timer); if (!initialized) { error.startupConnected=authenticated;reject(error); } });
      socket.on('close', () => {
        clearTimeout(timer);
        if (!initialized) { const error = new Error('后台启动已停止，请查看诊断日志后重试。'); error.code = 'ECONNRESET'; error.startupConnected=authenticated; reject(error); }
        if (this.socket !== socket) return;
        this.socket = null;
        const error = new Error('后台连接中断，操作结果需要重新核对；输入和已保存内容仍保留。');
        for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
        this.pending.clear();
        if (initialized && !this.detached && !this.upgrading) {
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
    return this.request(method, params);
  }
  request(method, params = {}) {
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
  async waitStopped() {
    const pid = this.pid || this.child?.pid;
    if (!pid) return;
    const deadline = Date.now()+30000;
    while (Date.now()<deadline) {
      try { process.kill(pid,0); }
      catch(error) { if(error.code==='ESRCH')return;throw error; }
      await delay(100);
    }
    throw new Error('后台仍在完成保存，尚未退出；请稍后重试或查看诊断日志。');
  }
  async stop() {
    this.detached = true;
    try {
      if (!this.socket?.writable) {
        const descriptor = this.descriptor();
        if (descriptor) {
          try { await this.connect(descriptor,{waitForReady:false}); }
          catch(error) { if(!['ECONNREFUSED','ECONNRESET'].includes(error.code))throw error; }
        }
      }
      if (this.socket?.writable) await this.request('app.shutdown');
      else if (this.child?.exitCode === null && !this.initialized) {
        // Our child has not published an authenticated endpoint: service data
        // initialization has not begun. Never terminate an attached service.
        this.child.kill();
      }
      await this.waitStopped(); this.detach();
    } catch(error) { this.detached=false;throw error; }
  }
}
module.exports = { Backend };
