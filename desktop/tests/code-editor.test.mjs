import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('source editors and multimedia skill configuration preserve drafts and resume the original task',{timeout:120000},async()=>{
  const root=path.resolve(directory,'../../.tmp/multimedia-editor');await mkdir(root,{recursive:true});
  const data=await mkdtemp(path.join(root,'ui-'));
  try {
    await build({root:path.resolve(directory,'..'),logLevel:'silent'});
    const env={...process.env,COWORK_APP_DATA_DIR:data,APPDATA:path.join(data,'roaming'),LOCALAPPDATA:path.join(data,'local'),TEMP:data,TMP:data,NO_PROXY:'127.0.0.1,localhost,::1'};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'code-editor.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/CODE_EDITOR_MEDIA_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
