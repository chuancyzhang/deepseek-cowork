const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { createWriteStream, mkdirSync, existsSync } = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

class Backend {
  constructor({ root, dataDir, resources, publish }) {
    this.pending = new Map(); this.publish = publish;
    mkdirSync(path.join(dataDir, 'logs'), { recursive: true });
    this.log = createWriteStream(path.join(dataDir, 'logs', 'desktop-service.log'), { flags: 'a' });
    const bundled = path.join(resources || '', 'service', process.platform === 'win32' ? 'cowork-service.exe' : 'cowork-service');
    const python = process.env.COWORK_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    const executable = resources ? bundled : python;
    if (!existsSync(executable)) throw new Error('本地服务尚未准备好。请运行 scripts/run_desktop.py，或使用完整的 Windows 发行包。');
    const args = resources ? ['--data-dir', dataDir] : ['-X', 'utf8', '-B', '-m', 'core.desktop_service', '--data-dir', dataDir];
    this.child = spawn(executable, args, { cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1', COWORK_APP_DATA_DIR: dataDir } });
    this.child.stderr.pipe(this.log, { end: false });
    this.ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('本地服务启动超时，请查看诊断日志。')), 90000);
      this.child.on('error', error => { clearTimeout(timer); reject(error); });
      createInterface({ input: this.child.stdout }).on('line', line => {
        let value;
        try { value = JSON.parse(line); } catch { this.log.write('Invalid service protocol line\n'); return; }
        if (value.event === 'ready') { clearTimeout(timer); resolve(value); }
        else if (value.id) {
          const pending = this.pending.get(value.id);
          if (!pending) return;
          clearTimeout(pending.timer); this.pending.delete(value.id);
          value.error ? pending.reject(new Error(value.error.message)) : pending.resolve(value.result);
        } else this.publish(value);
      });
      this.child.on('exit', (code, signal) => {
        this.exited = true;
        clearTimeout(timer);
        const error = new Error(`本地服务已退出（${code ?? signal}）。已保存内容保留在本机。`);
        reject(error);
        for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
        this.pending.clear(); this.publish({ event: 'service_error', error: error.message });
      });
    });
  }
  async call(method, params = {}) {
    await this.ready;
    if (!this.child.stdin.writable) throw new Error('本地服务连接已关闭。');
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('操作确认超时，请检查状态后再试。输入内容已保留。')); }, 60000);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ id, method, params }) + '\n', error => {
        if (error) { clearTimeout(timer); this.pending.delete(id); reject(error); }
      });
    });
  }
  async stop() { if (this.exited) return; await this.call('app.shutdown'); this.child.stdin.end(); }
}
module.exports = { Backend };
