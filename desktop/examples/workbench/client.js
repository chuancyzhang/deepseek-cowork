/** Cowork UI SDK v2. Copy this file into the local plugin package. */
export function createCowork() {
  let sequence=0,state=null,disposed=false;
  const pending=new Map(),subscribers=new Set(),commands=new Map();
  const request=(method,params={})=>new Promise((resolve,reject)=>{
    if(disposed){reject(Error('此插件实例已关闭。'));return;}
    const id=String(++sequence),timer=setTimeout(()=>{pending.delete(id);reject(Error('宿主尚未确认操作结果。请核对当前状态后继续，勿重复执行外部操作。'));},120000);
    pending.set(id,{resolve,reject,timer});parent.postMessage({channel:'cowork-plugin-v2',id,method,params},'*');
  });
  const listener=event=>{
    if(event.source!==parent||event.data?.channel!=='cowork-host-v2')return;
    const message=event.data;
    if(message.id&&pending.has(message.id)){
      const item=pending.get(message.id);pending.delete(message.id);clearTimeout(item.timer);
      message.error?item.reject(Error(message.error)):item.resolve(message.result);
    }
    if(message.event==='state'){state=message.data;for(const callback of subscribers)callback(state);}
    if(message.event==='command'){
      const callback=commands.get(message.data.command);
      if(callback)Promise.resolve().then(callback).catch(error=>request('reportError',{message:error.message}).catch(()=>{}));
    }
  };
  addEventListener('message',listener);
  const scope=conversationId=>({conversationId});
  return Object.freeze({
    ready:()=>request('ready'),
    snapshot:()=>request('snapshot'),
    subscribe(callback){subscribers.add(callback);if(state)callback(state);return ()=>subscribers.delete(callback);},
    conversations:Object.freeze({list:options=>request('conversations.list',options),get:conversationId=>request('conversations.get',scope(conversationId)),search:query=>request('conversations.search',{query}),create:workspace=>request('conversations.create',{workspace}),select:conversationId=>request('conversations.select',scope(conversationId)),rename:(conversationId,title)=>request('conversations.rename',{conversationId,title}),archive:conversationId=>request('conversations.archive',scope(conversationId)),delete:conversationId=>request('conversations.delete',scope(conversationId)),branch:(conversationId,messageId)=>request('conversations.branch',{conversationId,message_id:messageId}),preferences:(conversationId,value)=>request('conversations.preferences',{conversationId,value}),pin:(conversationId,pinned)=>request('conversations.pin',{conversationId,pinned}),restore:conversationId=>request('conversations.restore',{conversationId}),export:conversationId=>request('conversations.export',{conversationId}),reference:(conversationId,sourceId)=>request('conversations.reference',{conversationId,source_id:sourceId})}),
    runs:Object.freeze({send:conversationId=>request('runs.send',scope(conversationId)),stop:(conversationId,runId)=>request('runs.stop',{conversationId,runId}),respond:(conversationId,runId,requestId,response)=>request('runs.respond',{conversationId,runId,requestId,value:response})}),
    drafts:Object.freeze({get:conversationId=>request('drafts.get',scope(conversationId)),change:(value,conversationId)=>request('drafts.change',{conversationId,value}),save:conversationId=>request('drafts.save',scope(conversationId))}),
    editors:Object.freeze({get:options=>request('editors.get',options),open:(path,conversationId)=>request('editors.open',{path,conversationId}),change:(text,options)=>request('editors.change',{...options,text}),save:options=>request('editors.save',options),saveCopy:(name,options)=>request('editors.saveCopy',{...options,name})}),
    files:Object.freeze({list:(conversationId,path)=>request('files.list',{conversationId,path}),create:(conversationId,name)=>request('files.create',{conversationId,name}),describe:(conversationId,path)=>request('files.describe',{conversationId,path})}),
    tasks:Object.freeze({inspect:(conversationId,runId)=>request('tasks.inspect',{conversationId,runId}),list:conversationId=>request('tasks.list',{conversationId}),agent:(conversationId,agentId)=>request('tasks.agent',{conversationId,agentId}),stop:(conversationId,agentId,runId)=>request('tasks.stop',{conversationId,agentId,runId}),resume:(conversationId,agentId)=>request('tasks.resume',{conversationId,agentId})}),
    projects:Object.freeze({list:()=>request('projects.list'),save:value=>request('projects.save',value),archive:path=>request('projects.archive',{path}),restore:path=>request('projects.restore',{path})}),
    history:Object.freeze(Object.fromEntries(['prepare','edit','delete','undo'].map(name=>[name,params=>request('history.'+name,params)]))),
    management:Object.freeze({read:(action,params)=>request('management.read',{action,params}),write:(action,params)=>request('management.write',{action,params})}),
    navigation:Object.freeze({go:route=>request('navigation.go',{route})}),
    theme:Object.freeze({set:theme=>request('theme.set',{theme})}),
    dialogs:Object.freeze({confirm:(title,detail,choices)=>request('dialogs.confirm',{title,detail,choices}),attach:conversationId=>request('dialogs.attach',{conversationId}),choose:kind=>request('dialogs.choose',{kind}),save:name=>request('dialogs.save',{name}),workspace:()=>request('dialogs.workspace')}),
    state:Object.freeze({get:()=>request('state.get'),change:value=>request('state.change',{value})}),
    commands:Object.freeze({execute:command=>request('commands.execute',{command}),register(command,callback){commands.set(command,callback);return ()=>commands.delete(command);}}),
    desktop:Object.freeze(Object.fromEntries(['tabs','profiles','open','close','read','input','control','resize','reply','surface','navigate','command','clipboard','reveal'].map(name=>[name,params=>request('desktop.'+name,params)]))),
    dispose(){disposed=true;removeEventListener('message',listener);for(const item of pending.values()){clearTimeout(item.timer);item.reject(Error('插件已关闭，已提交操作的结果可在宿主中核对。'));}pending.clear();subscribers.clear();commands.clear();}
  });
}
