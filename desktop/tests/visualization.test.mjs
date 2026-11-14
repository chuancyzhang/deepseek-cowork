import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url), directory=path.dirname(fileURLToPath(import.meta.url));
test('sandboxed visualization runs under strict parent CSP, reports errors and respects read-only history',{timeout:25000},async()=>{
  const root=path.resolve(directory,'../..'), qa=path.join(root,'.tmp/visualization-tests');
  await mkdir(qa,{recursive:true});const data=await mkdtemp(path.join(qa,'case-'));
  try {
    const source=`import json,sys,pathlib
from core.inline_visualization import build_visualization_document
fragment='<div id="chart">saved chart</div>'
documents={name:build_visualization_document(fragment,{'selected':3},read_only=readonly,frame_only=True) for name,readonly in [('active',False),('readonly',True)]}
pathlib.Path(sys.argv[1],'documents.json').write_text(json.dumps(documents),encoding='utf-8')`;
    const built=spawnSync(process.env.COWORK_PYTHON || path.join(root,'.venv/Scripts/python.exe'),['-X','utf8','-B','-c',source,data],{cwd:root,windowsHide:true,encoding:'utf8',env:{...process.env,TEMP:data,TMP:data}});
    assert.equal(built.status,0,built.stderr || built.error?.message);
    const env={...process.env,COWORK_VIS_TEST_DATA:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'visualization.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/VISUALIZATION_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
