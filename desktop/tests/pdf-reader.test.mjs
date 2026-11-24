import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,readFile,writeFile,rm,cp} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
const require=createRequire(import.meta.url),directory=path.dirname(fileURLToPath(import.meta.url));

test('PDF reader preserves original pages, Chinese text, navigation and per-file state offline',{timeout:60000},async()=>{
  const desktop=path.resolve(directory,'..'),root=path.resolve(desktop,'../.tmp/pdf-reader-tests');
  await mkdir(root,{recursive:true});const data=await mkdtemp(path.join(root,'case-'));
  try{
    const source=name=>JSON.stringify(path.join(desktop,'src',name).replaceAll('\\','/'));
    await writeFile(path.join(data,'entry.tsx'),`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import PdfReader from ${source('PdfReader.tsx')};import {StoreContext} from ${source('host/context.tsx')};import {HostStore} from ${source('host/store.ts')};
import {OfficePreview} from ${source('FilePreview.tsx')};
import ${source('styles.css')};import ${source('workspace.css')};import ${source('plugins/plugins.css')};
const store=new HostStore();
window.renderCalls=[];
window.cowork={onEvents:()=>()=>{},call:async(method,p)=>{
 if(method==='file.render'){window.renderCalls.push(p);return {job_id:'preview-job'};}
 if(method==='jobs.get')return {id:'preview-job',status:window.failRender?'failed':'completed',error:'测试转换失败'};
 if(method==='file.render_result')return window.renderResult;
 if(method==='file.review_records')return p.conversation_id==='one'&&window.reviewRecords?window.reviewRecords:{batches:[],current:null};
 throw Error('Unexpected RPC '+method);
}};
function Fixture(){const [file,setFile]=useState(null),[identity,setIdentity]=useState('one'),[office,setOffice]=useState(null);window.deliverPdf=setFile;window.selectPdfIdentity=setIdentity;window.deliverOffice=setOffice;
return <StoreContext.Provider value={store}><div id="reader-fixture" style={{height:'100vh',display:'flex',flexDirection:'column',fontFamily:'"Microsoft YaHei UI",sans-serif','--bg':'#fff','--soft':'#f5f5f5','--text':'#333','--muted':'#777','--line':'#e5e5e5','--hover':'#eee'}}>{office?<OfficePreview key={identity} cid={identity} file={office}/>:file&&<PdfReader key={identity} url={file.url} identity={identity} name="产品方案.pdf"/>}</div></StoreContext.Provider>;}
createRoot(document.getElementById('root')).render(<Fixture/>);`);
    const html=(await readFile(path.join(desktop,'index.html'),'utf8')).replace('/src/main.tsx','./entry.tsx');await writeFile(path.join(data,'index.html'),html);
    await build({configFile:false,root:data,base:'./',logLevel:'silent',resolve:{alias:{react:path.join(desktop,'node_modules/react'),'react-dom':path.join(desktop,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},build:{outDir:path.join(data,'dist'),rollupOptions:{input:path.join(data,'index.html')}}});
    for(const name of ['cmaps','standard_fonts','wasm','iccs'])await cp(path.join(desktop,'node_modules/pdfjs-dist',name),path.join(data,'dist/pdfjs',name),{recursive:true});
    const env={...process.env,COWORK_PDF_TEST_DATA:data,TEMP:data,TMP:data};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[path.join(directory,'pdf-reader.fixture.cjs')],{env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    let output='';child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>output+=chunk);
    const code=await new Promise((resolve,reject)=>{child.on('exit',resolve);child.on('error',reject);});assert.equal(code,0,output);assert.match(output,/PDF_READER_FIXTURE_OK/);
  }finally{await rm(data,{recursive:true,force:true,maxRetries:3});}
});
