import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));
test('writing drafts and library navigation survive real desktop IPC and reload',{timeout:100000},async()=>{
  const root=path.resolve(directory,'../../.tmp/nov16-fixes');await mkdir(root,{recursive:true});
  const data=await mkdtemp(path.join(root,'ui-'));
  try {
    await build({root:path.resolve(directory,'..'),logLevel:'silent'});
    const env={...process.env,COWORK_APP_DATA_DIR:data,APPDATA:path.join(data,'roaming'),LOCALAPPDATA:path.join(data,'local'),TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'document-library.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',v=>output+=v);child.stderr.on('data',v=>output+=v);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/DOCUMENT_LIBRARY_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
