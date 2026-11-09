const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('cowork', Object.freeze({
  call: (method, params) => ipcRenderer.invoke('cowork:call', method, params),
  onEvents: callback => {
    const listener = (_event, events) => callback(events);
    ipcRenderer.on('cowork:events', listener);
    return () => ipcRenderer.removeListener('cowork:events', listener);
  }
}));
