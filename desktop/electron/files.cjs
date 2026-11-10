const path = require('node:path');
const fs = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
const { pathToFileURL } = require('node:url');

// Only backend-authorized files receive a URL. No renderer-supplied filesystem
// path is accepted by the protocol handler; relative HTML assets stay scoped.
class FileHost {
  constructor() { this.files = new Map(); }
  register(file) {
    const token = randomUUID();
    this.files.set(token, { path: file.path, generated: file.payload.preview_html });
    return { ...file, payload: { ...file.payload, preview_html: undefined,
      url: `cowork-local://file/${token}/${encodeURIComponent(path.basename(file.path))}` } };
  }
  draft(url, text) {
    const parsed = new URL(url);
    const entry = parsed.protocol === 'cowork-local:' && parsed.host === 'file' && this.files.get(parsed.pathname.split('/')[1]);
    if (!entry || !/\.html?$/i.test(entry.path) || typeof text !== 'string' || Buffer.byteLength(text) > 10 * 1024 * 1024) throw new Error('HTML 预览请求无效。');
    return this.register({path: entry.path, payload: {preview_html: text}}).payload.url;
  }
  async response(request, fetch) {
    const url = new URL(request.url);
    const parts = url.pathname.slice(1).split('/');
    const entry = url.host === 'file' && this.files.get(parts.shift());
    if (!entry) return new Response('文件预览已失效，请重新打开。', { status: 404 });
    const relative = decodeURIComponent(parts.join('/'));
    const directory = await fs.realpath(path.dirname(entry.path));
    let target;
    try { target = await fs.realpath(path.resolve(directory, relative)); }
    catch { return new Response('文件不存在。', { status: 404 }); }
    const relation = path.relative(directory, target);
    if (relation.startsWith('..') || path.isAbsolute(relation)) return new Response('不允许访问该文件。', { status: 403 });
    const isDocument = target === await fs.realpath(entry.path);
    if (!isDocument && !/\.(?:css|js|mjs|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|otf|mp4|webm|mp3|wav|json)$/i.test(target))
      return new Response('不支持的页面资源。', { status: 403 });
    if (isDocument && entry.generated) return new Response(entry.generated, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    return fetch(pathToFileURL(target).href, { headers: request.headers });
  }
}
module.exports = { FileHost };
