import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));
test('pasted images cross the production IPC, persist, reach vision and render in the existing timeline', {skip:!process.env.COWORK_PYTHON,timeout:90000},async()=>{
  const root=path.resolve(directory,'../../.tmp/attachments-tests');await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try {
    for(const [name,description] of [['image-review','检查图片内容并整理要点。'],['summary-writing','将材料整理成简明摘要。']]){
      const skill=path.join(data,'skills',name);await mkdir(skill,{recursive:true});
      await writeFile(path.join(skill,'SKILL.md'),`---\nname: ${name}\ndescription: ${description}\n---\n${description}\n`);
    }
    const env={...process.env,COWORK_APP_DATA_DIR:data,TEMP:data,TMP:data,APPDATA:data,LOCALAPPDATA:data,NO_PROXY:'127.0.0.1,localhost,::1'};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'attachments.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/ATTACHMENTS_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:6,retryDelay:300});}
});
