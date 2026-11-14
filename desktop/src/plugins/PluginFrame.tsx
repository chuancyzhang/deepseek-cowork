import {useEffect,useRef,useState} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type {Extension,Plugin} from '../types';
import type {Lease,PluginScope} from '../host/sdk';
import {useWorkbench} from '../host/Host';

export default function PluginFrame({plugin,extension,scope,mode='active',readyKey,hidden=false}:{
  plugin:Plugin;extension:Extension;scope:PluginScope;mode?:Lease['mode'];readyKey?:string;hidden?:boolean;
}) {
  const {sdk,registry,error,host}=useWorkbench(),frame=useRef<HTMLIFrameElement>(null);
  const lease=useRef<Lease>({active:true,mode,plugin,scope});lease.current.mode=mode;
  const [ready,setReady]=useState(false),[failure,setFailure]=useState('');
  const url=`cowork-plugin://${plugin.hash}/${extension.entry||plugin.entry}`;
  const identity=JSON.stringify([plugin.hash,extension.id,scope.conversationId,scope.filePath,scope.tabId]);
  if(readyKey)registry.expect(readyKey,plugin.hash);
  useEffect(()=>{if(ready&&readyKey&&registry.candidate?.plugin.hash===plugin.hash)registry.ready(readyKey,plugin.hash);},[ready,readyKey,registry.candidate?.plugin.hash]);
  useEffect(()=>{
    const instance:Lease={active:true,mode:lease.current.mode,plugin,scope,bounds:()=>frame.current?.getBoundingClientRect()};lease.current=instance;
    let raf=0,initialized=false,lastDocument='';
    const post=(value:unknown)=>{if(instance.active)frame.current?.contentWindow?.postMessage(value,'*');};
    const snapshot=()=>{
      if(!initialized)return;
      const value=sdk.snapshot(instance);
      if(plugin.hostApi===1){
        const data={name:value.editor?.file.name||'文稿',text:value.editor?.text||'',theme:value.theme,saved:value.editor?.text===value.editor?.saved},signature=JSON.stringify(data);
        if(signature!==lastDocument){lastDocument=signature;post({channel:'cowork-host-v1',event:'document',data});}
      }
      else post({channel:'cowork-host-v2',event:'state',data:value});
    };
    const schedule=()=>{if(!raf)raf=requestAnimationFrame(()=>{raf=0;snapshot();});};
    const unsubscribe=host.subscribeAll(schedule);
    const commandUnsubscribe=host.subscribe('plugin:command',()=>{
      const command=host.get<any>('plugin:command');
      if(instance.mode==='active'&&command?.pluginId===plugin.id&&command.extensionId===extension.id)post({channel:'cowork-host-v2',event:'command',data:command});
    });
    const draftUnsubscribe=scope.conversationId?sdk.controller.drafts.subscribe(scope.conversationId,schedule):sdk.controller.drafts.subscribeAll(schedule);
    const fail=(message:string)=>{setFailure(message);if(readyKey)registry.failed(message,plugin.hash);};
    const timer=setTimeout(()=>{if(!initialized)fail('界面未能就绪，可停用插件或恢复内置界面。');},15000);
    const listener=async(event:MessageEvent)=>{
      if(event.source!==frame.current?.contentWindow||!instance.active||event.data?.channel!==`cowork-plugin-v${plugin.hostApi}`)return;
      const {method,params,id}=event.data,source=event.source as Window;
      const reply=(result:unknown,reason?:string)=>{if(instance.active)source.postMessage({channel:`cowork-host-v${plugin.hostApi}`,id,result,error:reason},'*');};
      try {
        if(method==='ready'){
          initialized=true;clearTimeout(timer);setReady(true);setFailure('');snapshot();
          if(readyKey)registry.ready(readyKey,plugin.hash);reply({hostApi:plugin.hostApi});return;
        }
        if(typeof method!=='string'||typeof id!=='string')throw new Error('插件消息格式无效。');
        if(method==='reportError'){
          const message=String(params?.message||'插件操作未完成。').slice(0,2000);
          error(plugin.name+'：'+message);console.error('plugin_command_failed',plugin.id,extension.id,message);reply({reported:true});return;
        }
        if(plugin.hostApi===1){
          const mapped:Record<string,string>={'document.read':'editors.get','document.change':'editors.change','document.save':'editors.save'};
          if(method==='document.render'){
            const value=await sdk.invoke(instance,'editors.get');
            reply({html:renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkGfm]} components={{a:({children})=><span className="document-link">{children}</span>,img:({alt})=><span>[图片：{alt||'未命名'}]</span>}}>{value?.text||''}</ReactMarkdown>)});return;
          }
          if(!mapped[method])throw new Error('原写作插件不支持此操作。');
          if(method==='document.change'){
            const value=sdk.snapshot(instance);lastDocument=JSON.stringify({name:value.editor?.file.name||'文稿',text:params?.text,theme:value.theme,saved:params?.text===value.editor?.saved});
          }
          const result=await sdk.invoke(instance,mapped[method],params);
          reply(method==='document.read'?{text:result?.text,name:result?.file?.name}:result);return;
        }
        reply(await sdk.invoke(instance,method,params));
      }catch(reason){reply(null,(reason as Error).message);}
    };
    window.addEventListener('message',listener);
    return ()=>{instance.active=false;clearTimeout(timer);cancelAnimationFrame(raf);unsubscribe();commandUnsubscribe();draftUnsubscribe();window.removeEventListener('message',listener);
      if(scope.tabId&&extension.target==='browser')void window.cowork.call('browser.surface',{tab_id:''}).catch(reason=>error(reason.message));
    };
  },[identity,sdk,registry,host]);
  return <div className={'plugin-surface'+(hidden?' plugin-warm':'')} aria-hidden={hidden||undefined}>
    {!ready&&!failure&&<div className="editor-loading">正在打开界面…</div>}
    {failure&&<div className="plugin-failure" role="alert"><p>{failure}</p><button onClick={()=>host.set('host:recoveryOpen',true)}>界面恢复</button></div>}
    <iframe ref={frame} title={`${plugin.name} · ${extension.title||extension.target}`} sandbox="allow-scripts" src={url}/>
  </div>;
}
