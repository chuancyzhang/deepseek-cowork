const {randomBytes,createHash}=require('node:crypto');

const CSP="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; media-src 'none'; base-uri 'none'; form-action 'none'; sandbox allow-scripts";

// This function runs only in the opaque-origin preview, without a preload or host API.
function runtime(config) {
  const send=parent.postMessage.bind(parent),encode=JSON.stringify;
  const report=(type,detail={})=>send({token:config.token,type,...detail},'*');
  let started=false;
  addEventListener('message',event=>{
    if(event.source===parent&&event.data?.token===config.token&&event.data.type==='gate-request'&&!started){report('gate');return;}
    if(event.source!==parent||event.data?.token!==config.token||event.data.type!=='start'||started)return;
    started=true;
    document.open();
    const theme=document.createElement('style');
    const applyTheme=css=>{theme.textContent=String(css||'');};
    applyTheme(event.data.css);
    const clip=value=>value.length>4000?value.slice(0,3980)+'…（已截断）':value;
    const stringify=value=>{
      try {return clip((typeof value==='string'?value:typeof value==='undefined'?'undefined':typeof value==='function'?String(value):encode(value,(_key,item)=>typeof item==='bigint'?String(item)+'n':item))??String(value));}
      catch{return '[无法序列化的值]';}
    };
    let count=0,limited=false;
    const output=(level,args)=>{
      if(count++<100)report('console',{level,text:clip(args.map(stringify).join(' '))});
      else if(!limited){limited=true;report('console',{level:'warn',text:'输出已达 100 条上限；后续日志不再显示。'});}
    };
    for(const level of ['log','info','warn','error','debug'])console[level]=(...args)=>output(level,args);
    window.alert=value=>output('alert',['提示：',value]);
    const fail=(message,reason='runtime')=>report('error',{message:String(message).slice(0,4000),reason});
    addEventListener('error',event=>fail(event.message||'页面资源无法加载；预览仅支持自包含代码。',event.target===window?'runtime':'resource'),true);
    addEventListener('unhandledrejection',event=>fail(stringify(event.reason)));
    addEventListener('securitypolicyviolation',event=>fail('预览已阻止此操作或外部资源：'+event.violatedDirective,'policy'));
    addEventListener('click',event=>{const link=event.target.closest?.('a[href]');if(link&&!link.getAttribute('href').startsWith('#')){event.preventDefault();fail('预览内不支持跳转网页。源码和当前页面已保留。');}},true);
    addEventListener('message',event=>{
      if(event.source===parent&&event.data?.token===config.token&&event.data.type==='theme')applyTheme(event.data.css);
    });
    const heartbeat=setInterval(()=>report('heartbeat'),500);
    addEventListener('pagehide',()=>clearInterval(heartbeat),{once:true});
    let scheduled=false;
    const height=()=>{if(scheduled)return;scheduled=true;requestAnimationFrame(()=>{scheduled=false;report('height',{height:document.documentElement.scrollHeight,hasPage:[...document.body.childNodes].some(node=>node.nodeType===3?node.textContent.trim():node.nodeType===1&&!['SCRIPT','STYLE'].includes(node.tagName))});});};
    addEventListener('load',()=>{height();report('loaded');},{once:true});
    document.write(config.kind==='html'?config.source:'<!doctype html><html><head></head><body></body></html>');
    document.head.prepend(theme);
    if(config.kind==='js'){
      const script=document.createElement('script');script.textContent=config.source;document.body.append(script);
    }
    new ResizeObserver(height).observe(document.documentElement);
    document.close();
  });
  report('gate');
}

class SnippetHost {
  constructor({diagnostic=()=>{}}={}) {this.pages=new Map();this.diagnostic=diagnostic;}
  create(owner,{source,kind,scope}) {
    if(!['html','js'].includes(kind)||typeof source!=='string'||!source.trim()||Buffer.byteLength(source)>256*1024)throw Error('预览仅支持 256 KB 以内的 HTML 或浏览器 JavaScript。源码已保留。');
    if(typeof scope!=='string'||!scope||scope.length>500)throw Error('代码片段缺少所属消息，无法预览。');
    if(this.pages.size>=16)throw Error('同时打开的预览已达 16 个，请先停止一个预览。');
    const token=randomBytes(24).toString('hex'),url=`cowork-plugin://snippet-${token}/`;
    const config=JSON.stringify({source,kind,token}).replaceAll('<','\\u003c');
    const record={owner,scope,kind,token,url,hash:createHash('sha256').update(source).digest('hex'),html:`<!doctype html><html><head><meta charset="utf-8"></head><body><script>(${runtime.toString()})(${config})</script></body></html>`};
    this.pages.set(token,record);this.log(record,'created');return {token,url};
  }
  response(url) {
    const token=url.hostname.slice('snippet-'.length),record=this.pages.get(token);
    return new Response(record&&url.pathname==='/'?record.html:'预览已关闭。',{status:record&&url.pathname==='/'?200:404,headers:{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':CSP,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
  }
  start(owner,token) {
    const record=this.owned(owner,token);
    const frame=owner.mainFrame.framesInSubtree.find(frame=>frame.url===record.url);
    if(!frame||frame.processId===owner.mainFrame.processId)throw Error('未能建立独立预览进程，请重新运行。源码已保留。');
    record.frameId=frame.frameTreeNodeId;
    this.log(record,'started');return {started:true};
  }
  owned(owner,token) {const record=this.pages.get(token);if(!record||record.owner!==owner)throw Error('预览已失效，请重新运行。');return record;}
  event(owner,{token,status,reason}) {
    const record=this.owned(owner,token);
    if(!['loaded','error','timeout','navigation_blocked'].includes(status))throw Error('无效的预览状态。');
    if(!record[status]){record[status]=true;this.log(record,status,{reason:['runtime','resource','policy','isolation','navigation','timeout'].includes(reason)?reason:undefined});}return {recorded:true};
  }
  release(owner,token) {
    const record=this.pages.get(token);
    if(record){this.owned(owner,token);this.pages.delete(token);this.log(record,'released');}
    return {released:true};
  }
  clear(owner) {for(const [token,record] of this.pages)if(record.owner===owner)this.release(owner,token);}
  log(record,status,details={}) {this.diagnostic('snippet_'+status,{scope:record.scope,token:record.token,kind:record.kind,sha256:record.hash,...details});}
  attach(owner) {
    owner.on('will-frame-navigate',event=>{
      const record=[...this.pages.values()].find(record=>record.owner===owner&&record.frameId!=null&&(record.frameId===event.frame?.frameTreeNodeId||record.frameId===event.initiator?.frameTreeNodeId));
      if(record){event.preventDefault();this.event(owner,{token:record.token,status:'navigation_blocked'});owner.send('cowork:events',[{event:'snippet_navigation_blocked',data:{token:record.token}}]);}
    });
    // A parent's CSP may reject a navigation before will-frame-navigate is emitted.
    owner.on('did-fail-load',(_event,_code,_description,_url,isMainFrame,processId,routingId)=>{
      if(isMainFrame)return;
      const frame=require('electron').webFrameMain.fromId(processId,routingId);
      const record=[...this.pages.values()].find(record=>record.owner===owner&&record.frameId!=null&&record.frameId===frame?.frameTreeNodeId);
      if(record){this.event(owner,{token:record.token,status:'navigation_blocked'});owner.send('cowork:events',[{event:'snippet_navigation_failed',data:{token:record.token}}]);}
    });
    owner.on('did-start-navigation',event=>{if(event.isMainFrame)this.clear(owner);});
    owner.once('destroyed',()=>this.clear(owner));
  }
}
module.exports={SnippetHost};
