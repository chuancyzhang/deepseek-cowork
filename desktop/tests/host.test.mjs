import test from 'node:test';
import assert from 'node:assert/strict';
import {HostStore} from '../src/host/store.ts';
import {receiveConversation,receiveEvents,conversationAt} from '../src/host/conversations.ts';
import {HostSDK} from '../src/host/sdk.ts';
import {PluginRegistry} from '../src/plugins/registry.ts';
import {SaveCoordinator} from '../src/host/saveCoordinator.ts';
import {MessageProjection,mergeEvents} from '../src/runtime.ts';

const plugin={id:'test.plugin',name:'test',hash:'b',hostApi:2,stateVersion:1,entry:'index.html',capabilities:['drafts.read','drafts.write','document.read','document.edit','files.read'],contributes:[{id:'main',kind:'workbench',target:'main'}],token:'preview'};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

test('durable events stay in their own session and stale run callbacks cannot replace a newer run',()=>{
  const store=new HostStore();store.set('navigation:selected','b');
  receiveConversation(store,'a',{id:'a',meta:{},run:{id:'new',conversation_id:'a',status:'running',created_at:20,updated_at:20}});
  receiveEvents(store,[{event:'runtime',conversation_id:'a',run:{id:'old',conversation_id:'a',status:'completed',created_at:10,updated_at:30},data:{type:'content',run_id:'old',sequence:1,delta:'retained'}}]);
  assert.equal(conversationAt(store,'a').run.id,'new');
  assert.equal(store.get(store.sessionKey('events','a'))[0].delta,'retained');
  assert.equal(store.get(store.sessionKey('events','b')),undefined);
  assert.equal(store.get(store.sessionKey('current','a')).run,undefined);
});

test('incremental message projection preserves unchanged message identity and rebuilds after a history revision',()=>{
  const projection=new MessageProjection(),saved=[{id:'u',role:'user',content:'start',meta:{turn_id:'r'}}];
  let events=[{type:'content',sequence:1,run_id:'r',source_message_id:'a',delta:'one'},{type:'tool_call',sequence:2,run_id:'r',source_message_id:'a',data:{id:'tool'}},{type:'content',sequence:3,run_id:'r',source_message_id:'b',delta:'two'}];
  const first=projection.project(saved,events,'r');
  events=mergeEvents(events,[{type:'content',sequence:4,run_id:'r',source_message_id:'b',delta:' more'}]);
  const second=projection.project(saved,events,'r');
  assert.equal(second[1],first[1]);assert.notEqual(second[2],first[2]);assert.equal(second[2].content,'two more');
  assert.deepEqual(projection.project([{id:'replacement',role:'user',content:'edited'}],[]).map(item=>item.id),['replacement']);
});

test('SDK enforces capability and instance boundaries before effects, and fences a late file-open result',async()=>{
  const store=new HostStore(),sdk=new HostSDK(store),pending=deferred(),calls=[];
  globalThis.window={cowork:{call:async(method,params)=>{calls.push([method,params]);return pending.promise;}}};
  sdk.controller={selectedId:{current:'a'},registry:{switching:false,track:promise=>promise},drafts:{get:()=>({value:{text:'preserved',attachments:[]}})}};
  const lease={active:true,mode:'active',plugin,scope:{conversationId:'a',extensionId:'main'}};
  await assert.rejects(sdk.invoke({...lease,plugin:{...plugin,capabilities:[]}},'drafts.get'),/未声明/);
  await assert.rejects(sdk.invoke({...lease,mode:'warm'},'drafts.change',{value:{text:'bad',attachments:[]}}),/预览或切换/);
  await assert.rejects(sdk.invoke({...lease,active:false},'editors.open',{path:'x'}),/已失效/);
  assert.equal(calls.length,0);
  const opened=sdk.invoke(lease,'editors.open',{path:'x'});lease.active=false;
  pending.resolve({id:'file',path:'x',name:'x',payload:{text:'old'}});
  await assert.rejects(opened,/已失效/);assert.equal(store.get(store.sessionKey('editors','a')),undefined);
});

test('plugin activation drains in-flight mutations, waits for actual slot readiness and preserves before commit',async()=>{
  const registry=new PluginRegistry(),saves=new SaveCoordinator(),mutation=deferred(),calls=[];
  let saved=0;saves.register('work',async()=>{saved++;calls.push('save');});
  registry.inventory={plugins:[{...plugin,hash:'old'}],selected:{'workbench:main':['test.plugin/main']}};
  globalThis.requestAnimationFrame=callback=>setImmediate(callback);
  globalThis.window={cowork:{call:async(method)=>{calls.push(method);if(method==='plugins.stage')return plugin;if(method==='plugins.inventory')return {plugins:[plugin],selected:{'workbench:main':['test.plugin/main']}};return [];}}};
  registry.track(mutation.promise);const applied=registry.apply(plugin,saves);
  await new Promise(setImmediate);assert.deepEqual(calls,[]);
  mutation.resolve();
  while(!registry.candidate)await new Promise(setImmediate);
  registry.expect('actual-frame','b');
  await new Promise(setImmediate);assert.equal(registry.inventory.plugins[0].hash,'old');assert.ok(!calls.includes('plugins.install'));
  registry.ready('actual-frame','b');await applied;
  assert.equal(saved,2);assert.deepEqual(calls,['save','plugins.stage','save','plugins.install','plugins.inventory']);
  assert.equal(registry.inventory.plugins[0].hash,'b');assert.equal(registry.candidate,null);
});

test('a failed plugin candidate leaves selection and host edit state intact',async()=>{
  const registry=new PluginRegistry(),saves=new SaveCoordinator(),store=new HostStore();
  store.set('form:settings',{value:{text:'unfinished'},saved:{text:''}});
  const original={plugins:[{...plugin,hash:'old'}],selected:{'workbench:main':['test.plugin/main']}};registry.inventory=original;
  globalThis.requestAnimationFrame=callback=>setImmediate(callback);
  globalThis.window={cowork:{call:async(method)=>{assert.equal(method,'plugins.stage');return plugin;}}};
  const applying=registry.apply(plugin,saves);
  while(!registry.candidate)await new Promise(setImmediate);
  registry.failed('candidate failed','b');await assert.rejects(applying,/candidate failed/);
  assert.equal(registry.inventory,original);assert.equal(store.get('form:settings').value.text,'unfinished');assert.equal(registry.switching,false);
});

test('a whole workbench reads its active editor and reopening it preserves unsaved content',async()=>{
  const store=new HostStore(),sdk=new HostSDK(store),file={id:'original',path:'notes.md',name:'notes.md',payload:{kind:'text',text:'saved'}};
  store.set(store.sessionKey('editors','a'),[{file,text:'unsaved draft',saved:'saved'}]);
  store.set(store.sessionKey('activeFile','a'),'notes.md');
  sdk.controller={selectedId:{current:'a'},registry:{switching:false,track:promise=>promise}};
  globalThis.window={cowork:{call:async()=>({...file,id:'reopened'})}};
  const lease={active:true,mode:'active',plugin,scope:{extensionId:'main'}};
  assert.equal((await sdk.invoke(lease,'editors.get')).text,'unsaved draft');
  assert.equal((await sdk.invoke(lease,'editors.open',{path:'notes.md'})).id,'original');
  assert.equal((await sdk.invoke(lease,'editors.get')).text,'unsaved draft');
});

test('recovery waits for accepted edits and excludes another selection change',async()=>{
  const registry=new PluginRegistry(),saves=new SaveCoordinator(),edit=deferred(),calls=[];
  registry.track(edit.promise);saves.register('edits',async()=>calls.push('save'));
  globalThis.window={cowork:{call:async method=>{calls.push(method);return {plugins:[],selected:{}};}}};
  const recovery=registry.recover(saves);
  await assert.rejects(registry.disable('test.plugin',saves),/另一个界面/);
  assert.deepEqual(calls,[]);edit.resolve();await recovery;
  assert.deepEqual(calls,['save','plugins.recover']);assert.equal(registry.switching,false);
});

test('plugin theme changes preserve complete host settings for the restored builtin workbench',async()=>{
  const store=new HostStore(),sdk=new HostSDK(store),calls=[];
  const settings={models:[{id:'existing'}],selected_model_id:'existing',theme:'light',projects:[],god_mode:false};
  store.set('settings',settings);
  sdk.controller={selectedId:{current:'a'},registry:{switching:false,track:promise=>promise},setSettings:value=>store.set('settings',value)};
  globalThis.window={cowork:{call:async(method,params)=>{calls.push(method);if(method==='settings.theme'){assert.equal(params.theme,'dark');return {saved:true};}assert.equal(method,'settings.get');return {...settings,theme:'dark'};}}};
  const lease={active:true,mode:'active',plugin:{...plugin,capabilities:['theme']},scope:{extensionId:'main'}};
  await sdk.invoke(lease,'theme.set',{theme:'dark'});
  assert.deepEqual(calls,['settings.theme','settings.get']);
  assert.equal(store.get('settings').theme,'dark');
  assert.equal(store.get('settings').models.find(model=>model.id===store.get('settings').selected_model_id).id,'existing');
  assert.equal(store.get('settings').projects,settings.projects);
});
