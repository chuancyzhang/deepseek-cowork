import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('navigation keeps native menus, fences search replies and targets background runs without moving drafts',{skip:!process.env.COWORK_PYTHON,timeout:90000},async()=>{
  const root=path.resolve(directory,'../../.tmp/navigation-tests');await mkdir(root,{recursive:true});
  const data=await mkdtemp(path.join(root,'case-'));
  try{
    const env={...process.env,COWORK_APP_DATA_DIR:data,APPDATA:path.join(data,'roaming'),LOCALAPPDATA:path.join(data,'local'),TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'navigation.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/NAVIGATION_OK/);
    console.log(output.match(/NAVIGATION_OK[^\r\n]*/)?.[0]);
  }finally{await rm(data,{recursive:true,force:true,maxRetries:5,retryDelay:300});}
});
