import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {visualizationThemeCss} from '../src/theme.ts';
const require=createRequire(import.meta.url), directory=path.dirname(fileURLToPath(import.meta.url));
test('visualization preserves sandbox, theme redraw, display state and read-only history',{timeout:30000},async()=>{
  const root=path.resolve(directory,'../..'), qa=path.join(root,'.tmp/visualization-tests');
  await mkdir(qa,{recursive:true});const data=await mkdtemp(path.join(qa,'case-'));
  try {
    const theme=(dark,custom=false)=>visualizationThemeCss({getPropertyValue:name=>({
      '--chat-bg':custom?'#fff5e5':dark?'#242424':'#ffffff','--chat-text':dark?'#eeeeee':'#303030',
      '--muted':dark?'#b3b3b3':'#686868','--line':dark?'#555555':'#dedede','--soft':'#f5f5f5',
      '--hover':'#eeeeee','--text':dark?'#eeeeee':'#303030','--primary':dark?'#dddddd':'#404040',
      '--on-primary':dark?'#222222':'#ffffff','--error':'#bb3333','--link':custom?'#218041':'#1f5fa8',
      '--font-family':'"Microsoft YaHei UI", sans-serif','--font-scale':'1',
    }[name]||'')},dark);
    const fragment=`<section id="chart"><label>范围 <select aria-label="范围"><option value="3">A</option><option value="4">B</option><option value="0">空结果</option></select></label><canvas width="280" height="80"></canvas><p></p></section>
<script>(async()=>{const root=document.getElementById('chart'),select=root.querySelector('select'),canvas=root.querySelector('canvas'),context=canvas.getContext('2d');
const state=await cowork.loadState();select.value=String(state.selected);window.draws=0;
function draw(){window.draws++;const probe=document.createElement('span');root.append(probe);probe.style.color='var(--viz-series-1)';const color=getComputedStyle(probe).color;probe.remove();context.clearRect(0,0,280,80);context.fillStyle=color;context.fillRect(10,10,Number(select.value)*20,20);root.querySelector('p').textContent=select.value==='0'?'没有匹配记录':select.value;}
select.addEventListener('change',()=>{draw();cowork.saveState({selected:Number(select.value)});});addEventListener('cowork:themechange',draw);draw();window.ready=true;})();</script>`;
    const payload={fragment,light:theme(false),dark:theme(true),custom:theme(false,true)};
    const source=`import json,sys,pathlib
from core.inline_visualization import build_visualization_document
payload=json.load(sys.stdin)
documents={name:build_visualization_document(payload['fragment'],{'selected':3},theme_css=payload['light'],read_only=readonly,frame_only=True) for name,readonly in [('active',False),('readonly',True)]}
documents.update(dark=payload['dark'],custom=payload['custom'])
pathlib.Path(sys.argv[1],'documents.json').write_text(json.dumps(documents),encoding='utf-8')`;
    const python=process.env.COWORK_PYTHON || path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
    const built=spawnSync(python,['-X','utf8','-B','-c',source,data],{cwd:root,windowsHide:true,encoding:'utf8',input:JSON.stringify(payload),env:{...process.env,TEMP:data,TMP:data}});
    assert.equal(built.status,0,built.stderr || built.error?.message);
    const env={...process.env,COWORK_VIS_TEST_DATA:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'visualization.fixture.cjs')],{env:{...env,TEMP:data,TMP:data},windowsHide:true,timeout:25000,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>output+=value);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});
    assert.equal(code,0,output);assert.match(output,/VISUALIZATION_FIXTURE_OK/);
  } finally {await rm(data,{recursive:true,force:true,maxRetries:3});}
});
