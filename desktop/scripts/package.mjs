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
const env = {...process.env, COWORK_BUILD_DESKTOP_SERVICE:'1', TEMP:qa, TMP:qa,
  PYINSTALLER_CONFIG_DIR:path.join(qa,'pyinstaller-cache')};
function run(command, args) {
  const result = spawnSync(command, args, {cwd:root, env, stdio:'inherit', windowsHide:true});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`构建失败：${path.basename(command)}（${result.status}）`);
}
run(process.execPath, [path.join(desktop,'node_modules/typescript/bin/tsc'), '--noEmit', '--project', path.join(desktop,'tsconfig.json')]);
run(process.execPath, [path.join(root, 'web/editors/build.mjs')]);
const build = spawnSync(process.execPath, [path.join(desktop,'node_modules/vite/bin/vite.js'), 'build'], {cwd:desktop, env, stdio:'inherit', windowsHide:true});
if (build.status !== 0) throw new Error('界面构建失败。');
run(python, ['-m','PyInstaller','deepseek-cowork.spec','--noconfirm','--clean','--distpath',path.join(qa,'service-dist'),'--workpath',path.join(qa,'pyinstaller')]);
const pkg = JSON.parse(await fs.readFile(path.join(desktop,'package.json'),'utf8'));
const versionSource = await fs.readFile(path.join(root,'core/app_version.py'),'utf8');
const version = versionSource.match(/APP_VERSION\s*=\s*["']([^"']+)/)?.[1];
if (!version) throw new Error('无法读取 APP_VERSION。');
const zipDir = path.join(root, '.tmp/desktop/download');
let localZip = false;
try { localZip = (await fs.stat(path.join(zipDir, `electron-v${pkg.devDependencies.electron}-win32-x64.zip`))).isFile(); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const outputs = await packager({dir:desktop, out:path.join(desktop,'out'), name:pkg.productName, executableName:'deepseek-cowork', platform:'win32', arch:'x64',
  electronVersion:pkg.devDependencies.electron, ...(localZip ? {electronZipDir: zipDir} : {}),
  appVersion:version, overwrite:true, asar:true, prune:true, icon:path.join(root,'images/logo.ico'),
  ignore:[/^\/(src|tests|scripts|out)(\/|$)/, /^\/node_modules(\/|$)/, /^\/(tsconfig|vite\.config)/],
  afterCopy:[serialHooks([async (buildPath) => { const target=path.join(buildPath,'package.json'); const value=JSON.parse(await fs.readFile(target,'utf8')); value.version=version; value.dependencies={}; await fs.writeFile(target, JSON.stringify(value,null,2)); }])]
});
for (const output of outputs) {
  await fs.cp(path.join(qa,'service-dist/cowork-service'),path.join(output,'resources/service'),{recursive:true});
  await fs.cp(path.join(root,'web/editors/dist'),path.join(output,'resources/web/editors/dist'),{recursive:true});
  await fs.copyFile(path.join(root,'LICENSE'),path.join(output,'LICENSE-Cowork.txt'));
  await fs.copyFile(path.join(root,'images/logo.ico'),path.join(output,'resources/logo.ico'));
  await fs.writeFile(path.join(output,'desktop-release.json'), JSON.stringify({schema_version:1, app_layout:'electron', target:'windows-x64', version},null,2));
  run(python, ['-X', 'utf8', '-c', String.raw`
import hashlib, json, pathlib, sys, zipfile
source = pathlib.Path(sys.argv[1])
version = sys.argv[2]
files = sorted(p for p in source.rglob('*') if p.is_file())
for entry in files:
    parts = {part.lower() for part in entry.relative_to(source).parts}
    if parts & {'user_data', 'pyside6', 'shiboken6', '.env'} or entry.name.lower().startswith('qt6'):
        raise RuntimeError('发行包含用户数据或旧 UI 运行时：' + str(entry.relative_to(source)))
archive = source.parent / f'cowork-desktop-v{version}-windows-x64.zip'
pending = archive.with_suffix('.zip.partial')
with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
    for entry in files:
        target.write(entry, source.name + '/' + entry.relative_to(source).as_posix())
pending.replace(archive)
with archive.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
manifest = {'schema_version': 1, 'app_layout': 'electron', 'target_os': 'windows', 'arch': 'x64',
    'version': version, 'filename': archive.name, 'size': archive.stat().st_size, 'sha256': digest,
    'notes': 'DeepSeek Cowork：直接复用原数据，修复启动与完整退出，支持界面插件。'}
(source.parent / 'latest-desktop-windows-x64.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print('发行 ZIP：' + str(archive))
`, output, version]);
  console.log(`Windows 工作台：${path.join(output,'deepseek-cowork.exe')}`);
}
