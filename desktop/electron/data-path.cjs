const path = require('node:path');
const fs = require('node:fs');

function resolveDataDirectory({ explicit, packaged, executable, repository, appData }) {
  if (explicit) {
    if (!path.isAbsolute(explicit)) throw new Error('COWORK_APP_DATA_DIR 必须是绝对路径。');
    return path.normalize(explicit);
  }
  if (!packaged) return path.join(appData, 'DeepSeekCoworkNext');
  // Use the desktop executable's directory, never resources/service. Existing
  // portable installations keep their user_data beside the application.
  const portable = path.join(path.dirname(executable), 'user_data');
  if (fs.existsSync(portable)) return portable;
  return path.join(appData, 'DeepSeekCowork');
}

module.exports = { resolveDataDirectory };
