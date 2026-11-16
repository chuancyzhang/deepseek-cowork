import test from 'node:test';
import assert from 'node:assert/strict';
import {libraryCollections} from '../src/management/libraryCatalog.ts';
import {operation} from '../src/managementApi.ts';

test('a library shared through multiple spaces is listed once with its memberships',()=>{
  const items=libraryCollections({mine:[{id:'a',name:'个人库'}],others:[{id:'b',name:'共享库'}],shared:[{id:'a',organization_id:'one'},{id:'b',name:'共享库',organization_id:'one'},{id:'b',organization_id:'two'}],organizations:[{id:'one',name:'团队一'},{id:'two',name:'团队二'}]});
  assert.equal(items.length,2);assert.equal(items[0].group,'mine');assert.equal(items[1].group,'shared');
  assert.deepEqual(items[1].sharedWith,['团队一','团队二']);
  assert.equal(libraryCollections({mine:[{id:'personal'}],others:[{id:'space'}]})[1].group,'shared');
});

test('leaving a file cancels its job even if the receipt arrives after navigation',async()=>{
  let receipt;const cancelled=[];let listeners=0;
  global.window={cowork:{call:async(method,p)=>{
    if(method==='library.read')return new Promise(resolve=>receipt=resolve);
    if(method==='jobs.cancel'){cancelled.push(p.id);return {};}
    throw Error(method);
  },onEvents:()=>{listeners++;return ()=>listeners--;}}};
  const controller=new AbortController();const pending=operation('library.read',{},undefined,controller.signal);
  controller.abort();receipt({job_id:'old'});
  await assert.rejects(pending,{name:'AbortError'});
  assert.deepEqual(cancelled,['old']);assert.equal(listeners,0);
});

test('binary preview results are authorized by the host and storage errors settle observers',async()=>{
  const calls=[];let listener;let listeners=0;
  global.window={cowork:{call:async(method,p)=>{
    calls.push([method,p]);
    if(method==='library.read')return {job_id:'pdf'};
    if(method==='jobs.get')return {id:'pdf',status:'completed',result:{kind:'library_preview'}};
    if(method==='library.read_result')return {payload:{url:'cowork-local://authorized'}};
  },onEvents:callback=>{listener=callback;listeners++;return ()=>listeners--;}}};
  const result=await operation('library.read',{source:'weknora'});
  assert.equal(result.payload.url,'cowork-local://authorized');assert.equal(listeners,0);
  assert.deepEqual(calls.at(-1),['library.read_result',{source:'weknora',job_id:'pdf'}]);
  window.cowork.call=async method=>method==='library.read'?{job_id:'failed'}:{id:'failed',status:'running'};
  const pending=operation('library.read',{});await new Promise(resolve=>setImmediate(resolve));
  listener([{event:'job',data:{id:'failed',status:'failed',error:'无法保存'}}]);
  await assert.rejects(pending,/无法保存/);assert.equal(listeners,0);
});
