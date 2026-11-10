/* Isolated editor transport. No filesystem, Node, or application RPC access. */
window.connectDesktopBridge = function (name, connected) {
  const allowed = new Set(['beginLoad', 'appendLoadChunk', 'finishLoad', 'requestSave', 'preparePdfExport', 'command', 'dispose', 'setTheme', 'markClean', 'setReadOnly', 'getViewState', 'write', 'selectAll', 'paste', 'setInput', 'fit', 'focus']);
  window.addEventListener('message', async event => {
    if (event.source !== parent || event.data?.channel !== 'cowork-editor-host-v1') return;
    const { method, args = [], id } = event.data;
    try {
      if (!allowed.has(method)) throw new Error('不支持的编辑器操作。');
      const api = window.coworkEditor || window.coworkTerminal;
      if (typeof api?.[method] !== 'function') throw new Error('编辑器不支持此操作。');
      const result = await api[method](...args);
      if (id) parent.postMessage({ channel: 'cowork-editor-v1', method: 'commandResult', args: [id, result] }, '*');
    } catch (error) { parent.postMessage({ channel: 'cowork-editor-v1', method: 'reportError', args: [String(error.message || error)] }, '*'); }
  });
  connected(new Proxy({}, { get: (_target, method) => (...args) => parent.postMessage({ channel: 'cowork-editor-v1', method, args }, '*') }));
};
