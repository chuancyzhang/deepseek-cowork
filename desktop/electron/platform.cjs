// Native operations belonging to the Electron process are selected at startup.
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

async function atomicWrite(file, bytes) {
  const temporary = file + '.' + crypto.randomUUID() + '.tmp';
  const handle = await fs.open(temporary, 'wx');
  try { await handle.writeFile(bytes); await handle.sync(); }
  finally { await handle.close(); }
  try {
    await fs.rename(temporary, file);
    if (process.platform !== 'win32') {
      const directory = await fs.open(path.dirname(file), 'r');
      try { await directory.sync(); } finally { await directory.close(); }
    }
  } finally { await fs.rm(temporary, {force:true}); }
}
const platform = Object.freeze({
  id:process.platform, validatedTarget:process.platform === 'win32' ? 'windows-x64' : null,
  childOptions:{detached:process.platform !== 'win32', windowsHide:true},
  serviceName:process.platform === 'win32' ? 'cowork-service.exe' : 'cowork-service',
  pythonParts:process.platform === 'win32' ? ['Scripts','python.exe'] : ['bin','python'],
});
module.exports = {platform, atomicWrite};
