// One pipeline for DOM, native input, waits, files and decided batches.
const {randomUUID} = require('node:crypto');
const {nativeImage} = require('electron');
const fs = require('node:fs/promises');
const delay = ms => new Promise(resolve => setTimeout(resolve,ms));
const failure = (status,error,extra={}) => ({ok:false,status,error,executed:false,...extra});
class BrowserAutomation {
  constructor(host){this.host=host;this.sessions=new WeakSet();}
  command(item,method,args={},sessionId){return item.view.webContents.debugger.sendCommand(method,args,sessionId||undefined);}
  async attach(item){
    const wc=item.view.webContents;
    item.targets=new Map();item.frames=new Map();item.observations=new Map();item.screenshots=new Map();item.popups=[];item.dialogWaiters=new Set();
    if(!wc.debugger.isAttached())wc.debugger.attach('1.3');
    wc.debugger.on('message',(_event,method,p,sessionId)=>{
      if(method==='Target.attachedToTarget'){
        const ready=Promise.all([this.command(item,'Page.enable',{},p.sessionId),this.command(item,'Target.setAutoAttach',{autoAttach:true,waitForDebuggerOnStart:false,flatten:true},p.sessionId)]);
        item.targets.set(p.targetInfo.targetId,{sessionId:p.sessionId,ready});ready.catch(()=>{});
      }
      if(method==='Target.detachedFromTarget')for(const [id,t] of item.targets)if(t.sessionId===p.sessionId)item.targets.delete(id);
      if(method==='Page.javascriptDialogOpening'){
        item.dialog={id:randomUUID(),type:p.type,message:p.message,default_prompt:p.defaultPrompt||'',sessionId};
        for(const notify of item.dialogWaiters)notify();
      }
      if(method==='Page.javascriptDialogClosed')item.dialog=null;
    });
    await this.command(item,'Page.enable');
    await this.command(item,'Target.setAutoAttach',{autoAttach:true,waitForDebuggerOnStart:false,flatten:true});
    if(!this.sessions.has(wc.session)){
      this.sessions.add(wc.session);
      wc.session.on('will-download',(_event,download,source)=>{
        const owned=[...this.host.views.values()].find(v=>v.view.webContents===source);
        if(!owned)return;
        const pending=owned.download;
        if(!pending||pending.cancelled){if(owned.owner!=='human')download.cancel();return;}
        if(pending.item){download.cancel();return;}
        pending.item=download;download.setSavePath(pending.path);
        download.on('updated',()=>this.host.diagnostic('browser_download_progress',{conversation_id:owned.cid,tab_id:owned.id,request_id:pending.requestId,received:download.getReceivedBytes(),total:download.getTotalBytes()}));
        download.once('done',(_e,state)=>{pending.result={ok:state==='completed',status:state,path:download.getSavePath(),bytes:download.getReceivedBytes()};pending.resolve(pending.result);});
      });
    }
  }
  async frames(item){
    await item.protocolReady;const result=new Map();
    const add=async(tree,sessionId)=>{const frame=tree.frame,target=item.targets.get(frame.id);if(target&&target.sessionId!==sessionId){await target.ready;try{const nested=await this.command(item,'Page.getFrameTree',{},target.sessionId);nested.frameTree.frame.parentId=frame.parentId;await add(nested.frameTree,target.sessionId);return;}catch{}}
      result.set(frame.id,{id:frame.id,url:frame.url,parent:frame.parentId||'',sessionId});
      for(const child of tree.childFrames||[])await add(child,sessionId);};
    await add((await this.command(item,'Page.getFrameTree')).frameTree);
    // Out-of-process frames have their own Page tree and are omitted from the
    // parent target's tree. Enumerate only child targets attached to this page.
    for(const [id,target] of item.targets){
      if(result.has(id))continue;
      await target.ready;
      const tree=(await this.command(item,'Page.getFrameTree',{},target.sessionId)).frameTree;
      await add(tree,target.sessionId);
    }
    item.frames=result;return result;
  }
  async evaluate(item,frameId,expression,byValue=true){
    const frames=await this.frames(item),frame=frameId?frames.get(frameId):[...frames.values()].find(f=>!f.parent);
    if(!frame)throw Error('框架已关闭或跳转，请重新观察。');
    const {executionContextId}=await this.command(item,'Page.createIsolatedWorld',{frameId:frame.id,worldName:'cowork-automation'},frame.sessionId);
    const value=await this.command(item,'Runtime.evaluate',{expression:this.host.automation+'\n'+expression,contextId:executionContextId,returnByValue:byValue,awaitPromise:true},frame.sessionId);
    if(value.exceptionDetails)throw Error(value.exceptionDetails.text||'网页执行失败');
    return byValue?value.result.value:{...value.result,sessionId:frame.sessionId};
  }
  call(item,frame,name,args={}){return this.evaluate(item,frame,'window.'+name+'('+JSON.stringify(args)+')');}
  async check(request,item){await this.host.checked(request);if(item.dialog)throw Error('网页正在等待处理原生对话框。');}
  async observe(request,item,args={}){
    await this.host.checked(request);await item.protocolReady;
    if(item.dialog)return {ok:true,status:'dialog_open',tab_id:item.id,dialog:{...item.dialog,sessionId:undefined},owner:item.owner};
    if(!item.ready)return failure('loading','网页尚在加载，请等待 load 条件。',{url:item.view.webContents.getURL()});
    const epoch=item.revision,control=request.tab.control_revision||0;
    const frames=await this.frames(item),selected=args.frame_id||[...frames.values()].find(f=>!f.parent)?.id;
    const value=await this.call(item,selected,'coworkObserve',args);
    await this.check(request,item);if(epoch!==item.revision)throw Error('页面已跳转，请重新观察。');
    const version=randomUUID(),entry={epoch,control,frame:selected,dom:value.dom_version,document:value.document_id};
    item.observations.set(version,entry);while(item.observations.size>24)item.observations.delete(item.observations.keys().next().value);
    const result={...value,ok:true,tab_id:item.id,page_version:version,frame_id:selected,owner:item.owner,
      frames:[...frames.values()].map(f=>({id:f.id,parent_id:f.parent,url:f.url})),new_tabs:item.popups.splice(0)};
    delete result.dom_version;
    if(args.baseline){const prior=item.observations.get(args.baseline);if(prior?.summary&&prior.document===entry.document&&prior.summary===JSON.stringify([value.text,value.elements])){result.unchanged=true;result.baseline=args.baseline;delete result.text;delete result.elements;}}
    entry.summary=JSON.stringify([value.text,value.elements]);
    if(args.screenshot&&args.screenshot!=='none'){
      try{
        if(frames.get(selected)?.parent)throw Error('框架截图请在顶层页面获取，坐标统一使用顶层视口。');
        let screenshot,dimensions=value.viewport;
        if(args.screenshot==='full'||args.region){
          const metrics=await this.command(item,'Page.getLayoutMetrics'),size=metrics.cssContentSize;
          const clip=args.region?{...args.region,scale:1}:{x:0,y:0,width:Math.ceil(size.width),height:Math.ceil(size.height),scale:1};
          if(!['x','y','width','height'].every(k=>Number.isFinite(clip[k]))||clip.x<0||clip.y<0||clip.width<=0||clip.height<=0||clip.width*clip.height>40000000||clip.width>20000||clip.height>20000)throw Error('截图区域无效或超过 4000 万像素。');
          if(args.region&&(clip.x+clip.width>value.viewport.width||clip.y+clip.height>value.viewport.height))throw Error('区域超出当前视口。');
          const capture=await this.command(item,'Page.captureScreenshot',{format:'png',fromSurface:true,captureBeyondViewport:args.screenshot==='full',clip});
          screenshot=nativeImage.createFromBuffer(Buffer.from(capture.data,'base64'));dimensions={width:Math.ceil(clip.width),height:Math.ceil(clip.height)};
        }else screenshot=await this.host.capture(item.view.webContents);
        if(screenshot.isEmpty())throw Error('截图为空');
        await this.check(request,item);if(epoch!==item.revision)throw Error('截图期间页面已跳转');
        result.screenshot={type:'image_url',image_url:{url:screenshot.resize(dimensions).toDataURL()}};
        result.image_status='ready';result.coordinate_space='css';
        if(args.screenshot!=='full'&&!args.region){result.screenshot_id=randomUUID();item.screenshots.set(result.screenshot_id,{version,zoom:item.view.webContents.getZoomFactor(),viewport:value.viewport,image:screenshot.resize(value.viewport)});while(item.screenshots.size>8)item.screenshots.delete(item.screenshots.keys().next().value);}
      }catch(error){result.image_status='failed';result.screenshot_note='截图未完成：'+error.message+'。文字结果已保留。';this.host.diagnostic('browser_capture_failed',{request_id:request.id,tab_id:item.id,error:error.message});}
    }else result.image_status='not_requested';
    return result;
  }
  observation(request,item,args){
    const entry=item.observations.get(args.page_version);
    if(!entry||entry.epoch!==item.revision||entry.control!==(request.tab.control_revision||0))throw Error('观察凭证已失效，请重新观察并核对目标。');
    if(args.frame_id&&args.frame_id!==entry.frame)throw Error('观察凭证不属于此框架。');
    if(['click_at','drag_at'].includes(args.action)){
      const image=item.screenshots.get(args.screenshot_id);
      if(!image||image.version!==args.page_version||image.zoom!==item.view.webContents.getZoomFactor())throw Error('坐标操作需要对应当前视口的 screenshot_id。');
    }
    return entry;
  }
  async wait(request,item,condition={},timeout=5000,frame=''){
    const end=Date.now()+Math.max(0,Math.min(10000,Number(timeout)||0));
    do{await this.check(request,item);
      let result;
      if(condition.state==='load')result={ok:true,matched:item.ready};
      else if(condition.state==='url')result={ok:true,matched:condition.equals!==undefined?item.view.webContents.getURL()===condition.equals:item.view.webContents.getURL().includes(String(condition.includes||''))};
      else if(item.ready)result=await this.call(item,frame,'coworkCondition',condition);
      if(result?.matched)return {ok:true,status:'verified'};
      if(result?.ok===false)return result;
      if(Date.now()>=end)break;await delay(75);
    }while(true);
    return failure('timeout','等待条件超时。已派发动作不会自动重试。');
  }
  async coordinates(item,frameId,point){
    let frame=item.frames.get(frameId),p={x:point.x,y:point.y};
    while(frame?.parent){
      const parent=item.frames.get(frame.parent);if(!parent)throw Error('框架层级已变化');
      const owner=await this.command(item,'DOM.getFrameOwner',{frameId:frame.id},parent.sessionId);
      const {executionContextId}=await this.command(item,'Page.createIsolatedWorld',{frameId:parent.id,worldName:'cowork-automation'},parent.sessionId);
      const {object}=await this.command(item,'DOM.resolveNode',{backendNodeId:owner.backendNodeId,executionContextId},parent.sessionId);
      try{const result=await this.command(item,'Runtime.callFunctionOn',{objectId:object.objectId,arguments:[{value:p.x},{value:p.y}],returnByValue:true,
        functionDeclaration:`function(x,y){const r=this.getBoundingClientRect(),s=getComputedStyle(this);if(s.transform!=='none')return {error:'变换框架需要截图坐标操作'};const px=r.x+this.clientLeft+x,py=r.y+this.clientTop+y;const hit=this.ownerDocument.elementFromPoint(px,py);return hit===this?{x:px,y:py}:{error:'框架被遮挡或目标不在视口内'};}`},parent.sessionId);
        if(result.exceptionDetails||result.result.value?.error)throw Error(result.result.value?.error||'框架目标不可用');p=result.result.value;
      }finally{await this.command(item,'Runtime.releaseObject',{objectId:object.objectId},parent.sessionId).catch(()=>{});}
      frame=parent;
    }return p;
  }
  async dispatch(request,item,action,index){
    await this.check(request,item);
    const value=await this.host.backend.call('browser.host_dispatch',{id:request.id,step:index,action});
    request.dispatched=true;request.receiptId=value.receipt_id||`${request.id}:${index}`;
  }
  async receipt(request,result){
    if(!request.dispatched)return result;
    const value={...result,receipt_id:request.receiptId,executed:result.effect_state!=='not_dispatched',effect_state:result.effect_state||(result.status==='unknown'?'unknown':result.status==='verified'?'verified':'dispatched')};
    await this.host.backend.call('browser.host_receipt',{id:request.id,result:value});request.dispatched=false;request.lastReceipt=value;return value;
  }
  async effect(item,execute){
    let notify;
    const dialog=new Promise(resolve=>{notify=()=>resolve({ok:true,status:'dispatched',suspended:true});});
    item.dialogWaiters.add(notify);
    const pending={};item.pendingEffect=pending;
    pending.promise=execute().then(value=>{pending.result=value;return value;},error=>{pending.result={ok:false,status:'unknown',error:error.message};throw error;});
    try{return await Promise.race([pending.promise,dialog]);}
    finally{item.dialogWaiters.delete(notify);if(pending.result)item.pendingEffect=null;}
  }
  async native(item,action,args,checked,entry){
    const wc=item.view.webContents,point=checked.x===undefined?null:await this.coordinates(item,entry.frame,checked);
    const send=(method,p)=>this.command(item,method,p);
    if(action==='type'){await send('Input.insertText',{text:String(args.text||'')});return;}
    if(action==='key'){
      const parts=String(args.key||'').split('+'),key=parts.pop();let modifiers=0;
      for(const modifier of parts){const flag={Alt:1,Control:2,Ctrl:2,Meta:4,Shift:8}[modifier];if(!flag)throw Error('不支持的组合键');modifiers|=flag;}
      const keys={Enter:13,Tab:9,Escape:27,Backspace:8,Delete:46,Home:36,End:35,PageUp:33,PageDown:34,ArrowDown:40,ArrowUp:38,ArrowLeft:37,ArrowRight:39,Space:32};
      const code=keys[key]|| (key?.length===1?key.toUpperCase().charCodeAt(0):0);if(!code)throw Error('不支持此按键');
      const props={key:key==='Space'?' ':key,modifiers,windowsVirtualKeyCode:code};
      await send('Input.dispatchKeyEvent',{type:'keyDown',...props,...(!modifiers&&key==='Enter'?{text:'\r'}:{})});await send('Input.dispatchKeyEvent',{type:'keyUp',...props});return;
    }
    if(action==='hover'){await send('Input.dispatchMouseEvent',{type:'mouseMoved',...point});return;}
    if(action==='drag'||action==='drag_at'){
      const end=action==='drag_at'?{x:args.to_x,y:args.to_y}:await this.coordinates(item,entry.frame,args.destination);
      await send('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
      for(let n=1;n<=8;n++)await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x+(end.x-point.x)*n/8,y:point.y+(end.y-point.y)*n/8,button:'left',buttons:1});
      await send('Input.dispatchMouseEvent',{type:'mouseReleased',...end,button:'left',clickCount:1});return;
    }
    const count=action==='double_click'?2:1;
    for(let n=1;n<=count;n++){await send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:n});await send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:n});}
  }
  async action(request,item,args,index=0){
    request.lastReceipt=null;
    const entry=this.observation(request,item,args),operation={...args,dom_version:entry.dom};
    const supported=['click','double_click','hover','check','fill','type','key','select','scroll','scroll_into_view','click_at','drag','drag_at','upload'];
    if(!supported.includes(args.action))return failure('unsupported','不支持的网页操作。');
    const deadline=Date.now()+Math.min(3000,Math.max(0,Number(args.timeout_ms)||1000));let checked;
    do{await this.check(request,item);this.observation(request,item,args);checked=await this.call(item,entry.frame,'coworkAct',{...operation,action:'validate',target_action:args.action});
      if(checked.ok)break;if(!['element_obscured','element_disabled'].includes(checked.status)||Date.now()>=deadline)return checked;await delay(75);
    }while(true);
    if(args.action==='check'){
      if(!['checkbox','radio'].includes(checked.type))return failure('unsupported','目标不是勾选控件。');
      if(checked.checked===Boolean(args.checked))return {ok:true,status:'verified',executed:false};
    }
    if(args.action==='drag'){
      const target=await this.call(item,entry.frame,'coworkAct',{...operation,action:'validate',target_action:'click',target:args.to_target,element_id:''});
      if(!target.ok)return target;args={...args,destination:target};
    }
    if(args.action==='drag_at'&&(!Number.isFinite(args.to_x)||!Number.isFinite(args.to_y)))return failure('invalid_coordinate','拖拽终点无效。');
    if(['click_at','drag_at'].includes(args.action)){
      const image=item.screenshots.get(args.screenshot_id),points=[{x:args.x,y:args.y}];
      if(args.action==='drag_at'){
        const endpoint=await this.call(item,entry.frame,'coworkAct',{...operation,action:'validate',target_action:'click_at',x:args.to_x,y:args.to_y});
        if(!endpoint.ok)return endpoint;
        points.push({x:args.to_x,y:args.to_y});
      }
      const fresh=(await this.host.capture(item.view.webContents)).resize(image.viewport);
      for(const point of points){
        // Compare only the hit area; clocks, animations and unrelated updates
        // elsewhere on the page do not invalidate an observed coordinate.
        const x=Math.max(0,Math.floor(point.x)-24),y=Math.max(0,Math.floor(point.y)-24);
        const region={x,y,width:Math.min(48,image.viewport.width-x),height:Math.min(48,image.viewport.height-y)};
        if(!image.image.crop(region).toBitmap().equals(fresh.crop(region).toBitmap()))return failure('stale_page','操作位置附近的画面已变化，请重新截图并核对目标。');
      }
    }
    // Check a stable hit point twice; never retry a side effect.
    if(checked.x!==undefined){await delay(40);const next=await this.call(item,entry.frame,'coworkAct',{...operation,action:'validate',target_action:args.action});if(!next.ok)return next;
      if(Math.abs(next.x-checked.x)>.5||Math.abs(next.y-checked.y)>.5)return failure('element_moving','目标仍在移动，请等待后重新观察。');}
    const native=['click','double_click','hover','check','type','key','click_at','drag','drag_at'].includes(args.action);
    if(native&&checked.x!==undefined)await this.coordinates(item,entry.frame,checked);
    if(native){const prepared=await this.call(item,entry.frame,'coworkAct',{...operation,action:'prepare_native',target_action:args.action});if(!prepared.ok)return prepared;checked=prepared;}
    if(args.action==='upload'&&!checked.multiple&&args.files.length>1)return failure('invalid_files','此文件框只允许一个文件');
    let result;
    await this.dispatch(request,item,args.action,index);
    try{await this.check(request,item);this.observation(request,item,args);}
    catch(error){return this.receipt(request,{ok:false,status:'failed',effect_state:'not_dispatched',error:error.message});}
    try{
      if(native){result=await this.effect(item,async()=>{await this.native(item,args.action,args,checked,entry);return {ok:true,status:'dispatched'};});}
      else if(args.action==='upload'){
        const remote=await this.evaluate(item,entry.frame,'window.coworkElement('+JSON.stringify(args.target||{element_id:args.element_id})+')',false);
        if(!remote.objectId)throw Error('上传目标已失效');
        try{await this.command(item,'DOM.setFileInputFiles',{objectId:remote.objectId,files:args.files},remote.sessionId);result={ok:true,status:'dispatched',files:args.files};}
        finally{await this.command(item,'Runtime.releaseObject',{objectId:remote.objectId},remote.sessionId).catch(()=>{});}
      }else result=await this.effect(item,()=>this.call(item,entry.frame,'coworkAct',operation));
      if(result.ok&&!result.suspended&&args.action==='fill'){await delay(80);await this.check(request,item);result=await this.call(item,entry.frame,'coworkVerifyFill',{...operation,element_id:checked.element_id});}
      if(!result.ok&&result.executed===false){result.effect_state='not_dispatched';request.dispatched=true;}
      result=await this.receipt(request,result);
    }catch(error){result=await this.receipt(request,{ok:false,status:'unknown',error:error.message});}
    if(result.ok&&args.wait&&!item.dialog){const waiting=await this.wait(request,item,args.wait,args.wait_timeout_ms??5000,args.wait.frame_id||'');result.wait=waiting;
      if(!waiting.ok)result={...result,ok:false,status:'incomplete',error:'动作已派发，'+waiting.error+' 请继续等待或读取结果，不要重复派发。'};}
    if(args.observe!==false&&!item.dialog){try{result.observation=await this.observe(request,item,{frame_id:entry.frame});}catch(error){result.observation={ok:false,error:error.message};}}
    if(item.dialog)result.dialog={...item.dialog,sessionId:undefined};
    return result;
  }
  async handle(request,item){
    const args=request.arguments||{};await item.protocolReady;
    if(request.action==='observe')return this.observe(request,item,args);
    if(request.action==='browser_read')return item.dialog?{ok:true,status:'dialog_open',dialog:{...item.dialog,sessionId:undefined}}:this.call(item,args.frame_id,'coworkRead',args);
    if(request.action==='browser_wait')return this.wait(request,item,args.condition,args.timeout_ms??5000,args.frame_id);
    if(request.action==='browser_file'){
      if(args.operation==='upload')return this.action(request,item,{...args,action:'upload'});
      if(args.operation!=='download')return failure('unsupported','文件操作不支持。');
      if(item.download)throw Error('此页面已有下载等待');
      try{await fs.access(args.path);throw Error('下载目标已存在，请换用新路径');}catch(error){if(error.code!=='ENOENT')throw error;}
      const pending={path:args.path,requestId:request.id};pending.promise=new Promise(resolve=>{pending.resolve=resolve;});item.download=pending;
      try{const result=await this.action(request,item,{...args,action:'click',observe:false});if(!result.ok)return result;
        const end=Date.now()+Math.min(10000,Number(args.timeout_ms)||10000);
        while(!pending.result&&Date.now()<end){await this.check(request,item);await delay(75);}
        const downloaded=pending.result||failure('timeout','点击已派发，下载尚未完成。');
        if(!downloaded.ok)return {...result,ok:false,status:'incomplete',download:downloaded,error:'点击已派发，下载未完成；尚在进行的下载已取消。请核对结果，不要重复提交。'};
        return {...result,status:'verified',effect_state:'verified',download:downloaded};
      }finally{pending.cancelled=true;if(pending.item&&!pending.result)pending.item.cancel();item.download=null;}
    }
    if(request.action==='browser_action'){
      if(args.action==='dialog'){
        if(!item.dialog||item.dialog.id!==args.dialog_id)return failure('stale_dialog','对话框已经变化，请重新观察。');
        await this.host.checked(request);const registered=await this.host.backend.call('browser.host_dispatch',{id:request.id,step:0,action:'dialog'});request.dispatched=true;request.receiptId=registered.receipt_id||request.id+':0';
        await this.command(item,'Page.handleJavaScriptDialog',{accept:Boolean(args.accept),promptText:String(args.text||'')},item.dialog.sessionId);
        const pending=item.pendingEffect;
        if(pending){await Promise.race([pending.promise.catch(()=>{}),delay(2000)]);item.pendingEffect=null;
          if(!pending.result||!pending.result.ok)return this.receipt(request,{ok:false,status:'unknown',error:pending.result?.error||'原动作尚未返回，请核对页面，不要重放。'});}
        return this.receipt(request,{ok:true,status:'dispatched',resumed:pending?.result||null});
      }
      if(args.steps){if(!Array.isArray(args.steps)||!args.steps.length||args.steps.length>20)throw Error('批量操作限 1–20 步');
        const results=[];let version=args.page_version;
        for(let i=0;i<args.steps.length;i++){
          const step=args.steps[i];if(!step||['batch','dialog','resolve_unknown'].includes(step.action)||step.steps)throw Error('批量步骤无效');
          let result;
          try{result=await this.action(request,item,{...args,...step,steps:undefined,page_version:version,observe:true},i);}
          catch(error){result=failure(request.dispatched?'unknown':request.lastReceipt?'incomplete':'failed',error.message);
            if(request.dispatched)result=await this.receipt(request,{...result,effect_state:'unknown'});
            else if(request.lastReceipt)result={...request.lastReceipt,...result,executed:request.lastReceipt.executed};}
          results.push(result);
          if(!result.ok||result.dialog)return {ok:false,status:result.status==='unknown'?'unknown':'incomplete',results,completed:results.filter(r=>r.ok).length,not_executed:args.steps.length-i-1};
          if(!result.observation?.ok)return {ok:false,status:'incomplete',results,completed:results.length,not_executed:args.steps.length-i-1};version=result.observation.page_version;
        }return {ok:true,status:'completed',results,completed:results.length,not_executed:0};
      }
      return this.action(request,item,args);
    }
    throw Error('不支持的浏览器请求');
  }
}
module.exports={BrowserAutomation};
