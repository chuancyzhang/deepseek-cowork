const { contextBridge, ipcRenderer, webUtils } = require('electron');
contextBridge.exposeInMainWorld('cowork', Object.freeze({
  pathForFile: file => webUtils.getPathForFile(file),
  call: async (method, params) => {
    try { return await ipcRenderer.invoke('cowork:call', method, params); }
    catch (failure) {
      const message = String(failure?.message || failure)
        .replace(/^Error invoking remote method '[^']+':\s*/, '')
        .replace(/^(?:Error:\s*)+/, '').replace(/^•\s*/, '');
      throw new Error(message);
    }
  },
  onEvents: callback => {
    const listener = (_event, events) => callback(events);
    ipcRenderer.on('cowork:events', listener);
    return () => ipcRenderer.removeListener('cowork:events', listener);
  }
}));
