import test from 'node:test';
import assert from 'node:assert/strict';
import {HostStore} from '../src/host/store.ts';
import {receiveConversation,receiveEvents,conversationAt,presentOpenedBrowser} from '../src/host/conversations.ts';
import {readVideoTaskLayout, videoPathKey, videoTaskLayout} from '../src/host/videoTasks.ts';
import {HostSDK} from '../src/host/sdk.ts';
import {PluginRegistry} from '../src/plugins/registry.ts';
import {SaveCoordinator} from '../src/host/saveCoordinator.ts';
import {MessageProjection,mergeEvents,messageGroups} from '../src/runtime.ts';

test('browser presentation opens once without overriding selection, closed panels or background chats',()=>{
  const host=new HostStore(),initial=()=>({selected:'new'});
  host.set('navigation:selected','a');host.set('navigation:route','chat');
  host.set('runs',{a:{id:'r'},b:{id:'s'}});
  const event={event:'browser_opened',conversation_id:'a',run_id:'r',data:{tab_id:'web'}};
  assert.equal(presentOpenedBrowser(host,event,initial),true);
  assert.equal(host.get(host.sessionKey('workspaceOpen','a')),true);
  assert.equal(host.get(host.sessionKey('workspaceView','a')).selected,'workbench:web');
  host.set(host.sessionKey('workspaceOpen','a'),false);
  assert.equal(presentOpenedBrowser(host,{...event,data:{tab_id:'second'}},initial),false);
  assert.equal(host.get(host.sessionKey('workspaceOpen','a')),false);
  assert.equal(presentOpenedBrowser(host,{...event,conversation_id:'b',run_id:'s'},initial),false);
  assert.equal(host.get('navigation:selected'),'a');
  for(const patch of [{selected:'file:editing.md'},{selected:'new',browserPresentationSuppressed:true}]){
    host.set(host.sessionKey('workspaceView','a'),patch);
    assert.equal(presentOpenedBrowser(host,event,initial),false);
  }
  host.set(host.sessionKey('workspaceView','a'),initial());
  assert.equal(presentOpenedBrowser(host,{...event,run_id:'old'},initial),false);
});

test('media task state belongs to its conversation and newer events survive late snapshots',()=>{
  const host=new HostStore();host.set('navigation:selected','b');
  receiveConversation(host,'a',{id:'a',meta:{},media_tasks:[]});
  const task={id:'task',conversation_id:'a',capability:'video_generate',status:'running',created_at:1,updated_at:2,files:[]};
  receiveEvents(host,[{event:'media_task',conversation_id:'a',data:task}]);
  receiveConversation(host,'a',{id:'a',meta:{},media_tasks:[{...task,status:'queued',updated_at:1}]});
  assert.equal(conversationAt(host,'a').media_tasks[0].status,'running');
  assert.equal(host.get('navigation:selected'),'b');
  assert.deepEqual(host.get(host.sessionKey('media_tasks','b'),[]),[]);
  receiveEvents(host,[{event:'media_task',conversation_id:'b',data:{...task,status:'completed',updated_at:3}}]);
  assert.deepEqual(host.get(host.sessionKey('media_tasks','b'),[]),[]);
  receiveEvents(host,[{event:'media_task',conversation_id:'a',data:{...task,status:'completed',updated_at:4}}]);
  assert.equal(conversationAt(host,'a').media_tasks[0].status,'completed');
});

test('video cards follow their creation calls across pagination and versioned retries',()=>{
  const host = new HostStore();
  const firstPath = 'D:\\聊天目录\\media\\first\\video.mp4';
  const secondPath = 'D:\\聊天目录\\media\\second\\video.mp4';
  const task = {conversation_id:'chat', run_id:'same-run', capability:'video_generate', updated_at:1, status:'running', files:[]};
  const video = (id, callId, files) => ({...task, id, tool_call_id:callId, files, revision:1, created_at:id==='first' ? 1 : 2});
  const first = video('first','call-one',[firstPath]);
  const second = video('second','call-two',[secondPath]);
  const messages = [
    {id:'reply-one',role:'assistant',meta:{turn_id:'same-run'},tool_calls:[{id:'call-one',function:{name:'media_create'}}]},
    {id:'next-request',role:'user',content:'再生成一段'},
    {id:'reply-two',role:'assistant',meta:{turn_id:'same-run'},tool_calls:[{id:'call-two',function:{name:'media_create'}}]},
    {id:'normal-request',role:'user',content:'哪里的夜景好看？'},
    {id:'normal-reply',role:'assistant',content:'南滨路。'},
  ];
  const event = data => receiveEvents(host,[{event:'media_task',conversation_id:'chat',data}]);
  event(second); event(first);
  let read = readVideoTaskLayout(host,'chat',messageGroups(messages.slice(1)));
  assert.deepEqual(read().unplaced,['first']);
  assert.deepEqual([...read().groups],[['reply-two',['second']]]);
  read = readVideoTaskLayout(host,'chat',messageGroups(messages));
  const layout = read();
  assert.deepEqual([...layout.groups],[['reply-one',['first']],['reply-two',['second']]]);
  assert.deepEqual(layout.unplaced,[]);
  const parallel = {...messages[0],tool_calls:[...messages[0].tool_calls,...messages[2].tool_calls]};
  assert.deepEqual([...videoTaskLayout([second,first],messageGroups([parallel]),'chat').groups],[['reply-one',['first','second']]]);
  assert.equal(layout.files.get(videoPathKey('file:///d:/%E8%81%8A%E5%A4%A9%E7%9B%AE%E5%BD%95/media/first/video.mp4')).taskId,'first');
  assert.equal(layout.files.get(videoPathKey('./media/second/video.mp4','D:\\聊天目录')).taskId,'second');
  assert.notEqual(videoPathKey('D:\\a%20b\\video.mp4'),videoPathKey('file:///D:/a%20b/video.mp4'));
  event({...second,status:'not_submitted',revision:2});
  event({...second,status:'queued',revision:3,updated_at:0});
  receiveConversation(host,'chat',{id:'chat',meta:{},media_tasks:[{...second,status:'not_submitted',revision:2,updated_at:999}]});
  assert.equal(conversationAt(host,'chat').media_tasks.find(item=>item.id==='second').status,'queued');
  assert.equal(read(),layout);
  const historical = [{id:'old-result',role:'tool',tool_call_id:'call-one',meta:{turn_id:'same-run',tool_name:'media_create'},result_obj:{task_id:'first'}}];
  assert.deepEqual([...videoTaskLayout([{...first,tool_call_id:undefined}],messageGroups(historical),'chat').groups],[['old-result',['first']]]);
});
import {activeReview,reviewTitle} from '../src/reviews.ts';

test('review status uses durable revisions and the active conversation/run even before a snapshot loads',()=>{
  const store=new HostStore();
  const draft={id:'office',kind:'office',title:'报告',format:'docx',status:'generating',revision:2,run_id:'run-a'};
  const run={id:'run-a',conversation_id:'a',status:'running',created_at:1,updated_at:2};
  receiveEvents(store,[{event:'review_changed',conversation_id:'a',data:{...draft,status:'ready',revision:3}}]);
  receiveConversation(store,'a',{id:'a',meta:{},run,reviews:[draft]});
  receiveConversation(store,'b',{id:'b',meta:{},run:{...run,id:'run-b',conversation_id:'b'},reviews:[]});
  assert.equal(conversationAt(store,'a').reviews[0].status,'ready');
  assert.equal(conversationAt(store,'b').reviews.length,0);
  assert.equal(activeReview(conversationAt(store,'a').reviews,run,'a').id,'office');
  assert.equal(reviewTitle(draft),'报告.docx');
  assert.equal(reviewTitle({...draft,title:'报告.DOCX'}),'报告.DOCX');
  for(const other of [{...run,id:'next-run'}, {...run,conversation_id:'b'}, {...run,status:'completed'}, {...run,status:'failed'}, {...run,status:'interrupted'}]) {
    assert.equal(activeReview(conversationAt(store,'a').reviews,other,'a'),undefined);
  }
  assert.equal(activeReview([draft],{...run,status:'stopping'},'a').id,'office');
  const before = conversationAt(store,'a').reviews;
  receiveEvents(store,[{event:'review_changed',conversation_id:'a',data:{...draft,revision:1}}]);
  receiveConversation(store,'a',{id:'a',meta:{},run,reviews:[]});
  assert.equal(conversationAt(store,'a').reviews,before);
  assert.equal(conversationAt(store,'a').reviews[0].status,'ready');
  receiveEvents(store,[{event:'review_changed',conversation_id:'b',data:{...draft,kind:'skill',run_id:'run-b',revision:10}}]);
  assert.equal(conversationAt(store,'a').reviews[0].kind,'office');
  assert.equal(activeReview(conversationAt(store,'b').reviews,conversationAt(store,'b').run,'b').kind,'skill');
});

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


test('legacy IM navigation drafts are offered without marking a fresh visit dirty',async()=>{
  const {prepareFormDraft,restoreFormRecovery,sameFormValue}=await import('../src/host/forms.ts');
  const initial={enabled_provider:'wechat','feishu:app_id':'','dingtalk:client_id':'','dingtalk:client_secret':'','wecom:secret':''};
  const legacy={enabled_provider:'','feishu:app_id':'','dingtalk:client_id':''};
  const form=prepareFormDraft(initial,legacy,true);
  assert.ok(sameFormValue(form.value,form.saved));assert.equal(form.value.enabled_provider,'wechat');
  assert.equal(form.recovery.value.enabled_provider,'');
  const restored=restoreFormRecovery(form);assert.equal(restored.value.enabled_provider,'');
  assert.ok(!sameFormValue(restored.value,restored.saved));assert.equal(restored.recovery,undefined);
  assert.ok(prepareFormDraft(initial,JSON.parse(JSON.stringify(form)),true).recovery);
});

test('form baselines preserve real edits without restoring stale unchanged fields',async()=>{
  const {prepareFormDraft,sameFormValue,publicFormDraft}=await import('../src/host/forms.ts');
  const saved={enabled_provider:'wechat',label:'old',token:''},value={...saved,label:'edited',token:'private-fixture'};
  const cached=publicFormDraft({version:1,value,saved,recovery:{value,saved}},['token']);
  assert.ok(!JSON.stringify(cached).includes('private-fixture'));assert.ok(!Object.hasOwn(cached.saved,'token'));
  const next=prepareFormDraft({...saved,enabled_provider:'feishu',newField:'current'},cached,true);
  assert.equal(next.value.enabled_provider,'feishu');assert.equal(next.value.newField,'current');assert.equal(next.value.label,'edited');assert.equal(next.value.token,'');
  const applied=prepareFormDraft({...saved,label:'edited'}, {...cached,recovery:undefined},true);
  assert.ok(sameFormValue(applied.value,applied.saved));
  assert.ok(sameFormValue({a:1,b:{x:2,y:3}},{b:{y:3,x:2},a:1}));
});

test('legacy host snapshots recover only actual edits, while clean snapshots follow current settings',async()=>{
  const {prepareFormDraft,sameFormValue}=await import('../src/host/forms.ts');
  const saved={enabled_provider:'feishu',label:'old'},initial={enabled_provider:'wechat',label:'current'};
  const clean=prepareFormDraft(initial,{value:saved,saved},true);
  assert.ok(sameFormValue(clean.value,initial));assert.equal(clean.recovery,undefined);
  const dirty=prepareFormDraft(initial,{value:{...saved,label:'unfinished'},saved},true);
  assert.ok(sameFormValue(dirty.value,initial));assert.equal(dirty.recovery.value.label,'unfinished');assert.equal(dirty.recovery.value.enabled_provider,'wechat');
});

// A get response may arrive after a dismiss or a newly prepared package event.
test('update snapshots cannot revive a dismissed notice or replace a newer package',async()=>{
  const {receiveUpdate}=await import('../src/host/updates.ts');
  const store=new HostStore();
  receiveUpdate(store,{revision:'100000000000000001',notice:true,version:'5.4.1'});
  receiveUpdate(store,{revision:'100000000000000002',notice:false,version:'5.4.1'});
  receiveUpdate(store,{revision:'100000000000000001',notice:true,version:'5.4.1'});
  assert.equal(store.get('updates').notice,false);
  receiveUpdate(store,{revision:'100000000000000003',notice:true,version:'5.4.2'});
  assert.equal(store.get('updates').version,'5.4.2');
});
