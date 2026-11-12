import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));
test('real Electron loads local JS modules and CSS in an isolated v2 plugin under strict parent CSP',{timeout:20000},async()=>{
  const root=path.resolve(directory,'../../.tmp/plugin-page');await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'test-'));
  try{
    const env={...process.env,COWORK_PLUGIN_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'plugin-page.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/PLUGIN_PAGE_FIXTURE_OK/);
  }finally{await rm(data,{recursive:true,force:true,maxRetries:3});}
});
