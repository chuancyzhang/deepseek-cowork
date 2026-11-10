const { contextBridge, ipcRenderer, webUtils } = require('electron');
contextBridge.exposeInMainWorld('cowork', Object.freeze({
  pathForFile: file => webUtils.getPathForFile(file),
  call: (method, params) => ipcRenderer.invoke('cowork:call', method, params),
  onEvents: callback => {
    const listener = (_event, events) => callback(events);
    ipcRenderer.on('cowork:events', listener);
    return () => ipcRenderer.removeListener('cowork:events', listener);
  }
}));
