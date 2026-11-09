import { packager, serialHooks } from '@electron/packager';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(desktop);
if (process.platform !== 'win32') throw new Error('Windows 包需要在 Windows 上构建。');
const python = process.env.COWORK_PYTHON || path.join(root, '.venv/Scripts/python.exe');
const qa = path.join(root, '.tmp/desktop-build');
await fs.mkdir(qa, {recursive: true});
const env = {...process.env, COWORK_BUILD_DESKTOP_SERVICE:'1', TEMP:qa, TMP:qa};
function run(command, args) {
  const result = spawnSync(command, args, {cwd:root, env, stdio:'inherit', windowsHide:true});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`构建失败：${path.basename(command)}（${result.status}）`);
}
run(process.execPath, [path.join(desktop,'node_modules/typescript/bin/tsc'), '--noEmit', '--project', path.join(desktop,'tsconfig.json')]);
const build = spawnSync(process.execPath, [path.join(desktop,'node_modules/vite/bin/vite.js'), 'build'], {cwd:desktop, env, stdio:'inherit', windowsHide:true});
if (build.status !== 0) throw new Error('界面构建失败。');
run(python, ['-m','PyInstaller','deepseek-cowork.spec','--noconfirm','--distpath',path.join(qa,'service-dist'),'--workpath',path.join(qa,'pyinstaller')]);
const pkg = JSON.parse(await fs.readFile(path.join(desktop,'package.json'),'utf8'));
const versionSource = await fs.readFile(path.join(root,'core/app_version.py'),'utf8');
const version = versionSource.match(/APP_VERSION\s*=\s*["']([^"']+)/)?.[1];
if (!version) throw new Error('无法读取 APP_VERSION。');
const zipDir = path.join(root, '.tmp/desktop/download');
let localZip = false;
try { localZip = (await fs.stat(path.join(zipDir, `electron-v${pkg.devDependencies.electron}-win32-x64.zip`))).isFile(); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const outputs = await packager({dir:desktop, out:path.join(desktop,'out'), name:'Cowork', platform:'win32', arch:'x64',
  electronVersion:pkg.devDependencies.electron, ...(localZip ? {electronZipDir: zipDir} : {}),
  appVersion:version, overwrite:true, asar:true, prune:true, icon:path.join(root,'images/logo.ico'),
  ignore:[/^\/(src|tests|scripts|out)(\/|$)/, /^\/node_modules(\/|$)/, /^\/(tsconfig|vite\.config)/],
  afterCopy:[serialHooks([async (buildPath) => { const target=path.join(buildPath,'package.json'); const value=JSON.parse(await fs.readFile(target,'utf8')); value.version=version; value.dependencies={}; await fs.writeFile(target, JSON.stringify(value,null,2)); }])]
});
for (const output of outputs) {
  await fs.cp(path.join(qa,'service-dist/cowork-service'),path.join(output,'resources/service'),{recursive:true});
  await fs.copyFile(path.join(root,'LICENSE'),path.join(output,'LICENSE-Cowork.txt'));
  console.log(`Windows 工作台：${path.join(output,'Cowork.exe')}`);
}
