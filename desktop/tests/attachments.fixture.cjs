const {app,BrowserWindow}=require('electron');
const http=require('node:http'),fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const requests=[];let imagePath='',window,server;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const deadline=setTimeout(()=>{console.error('attachment fixture timeout');app.exit(1);},75000);
// A fixture script is not launched from package.json; provide the real app version.
app.getVersion=()=>require('../package.json').version;
// Use the real entry, preload, IPC allowlist, Python service and React workbench.
app.on('browser-window-created',(_event,value)=>{window=value;});
require('../electron/main.cjs');
app.whenReady().then(async()=>{
  server=http.createServer((req,res)=>{let raw='';req.on('data',chunk=>raw+=chunk);req.on('end',()=>{
    const payload=JSON.parse(raw);requests.push(payload);
    res.writeHead(200,{'Content-Type':'text/event-stream'});
    const viewed=payload.messages.some(message=>message.role==='tool');
    const delta=viewed?{content:'已查看图片，图表内容已保留。'}:{tool_calls:[{index:0,id:'view-image-test',type:'function',function:{name:'view_image',arguments:JSON.stringify({path:imagePath})}}]};
    res.end('data: '+JSON.stringify({id:'fixture',choices:[{index:0,delta,finish_reason:null}]})+'\n\ndata: '+JSON.stringify({id:'fixture',choices:[{index:0,delta:{},finish_reason:viewed?'stop':'tool_calls'}]})+'\n\ndata: [DONE]\n\n');
  });});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  while(!window)await delay(30);
  const read=code=>window.webContents.executeJavaScript(code);
  const until=async(code)=>{for(let i=0;i<700;i++){try{if(await read(code))return;}catch{}await delay(40);}throw Error('Timed out: '+code+' '+await read("JSON.stringify({text:document.body.textContent,images:[...document.querySelectorAll('.image-attachment')].map(e=>e.outerHTML),messages:[...document.querySelectorAll('.message')].map(e=>({class:e.className,text:e.textContent}))})"));};
  await until("!!window.cowork&&!!document.querySelector('textarea[aria-label=\"消息输入\"]')");
  const settings=await read(`window.cowork.call('settings.save_model',{name:'图片验收',model_name:'fixture',api_key:'local-only',base_url:'http://127.0.0.1:${server.address().port}/v1',supports_vision:true})`);
  await read("window.cowork.call('settings.permission',{enabled:true})");
  window.webContents.reload();
  await delay(300);
  await until("!!document.querySelector('textarea[aria-label=\"消息输入\"]')&&document.body.innerText.includes('图片验收')");
  const paste=()=>read(`(()=>{const canvas=document.createElement('canvas');canvas.width=320;canvas.height=200;const c=canvas.getContext('2d');c.fillStyle='#f5f5f5';c.fillRect(0,0,320,200);c.fillStyle='#404040';c.font='22px sans-serif';c.fillText('图片预览验收',40,65);c.fillRect(40,100,65,55);c.fillRect(125,120,65,35);c.fillRect(210,85,65,70);const bytes=Uint8Array.from(atob(canvas.toDataURL('image/png').split(',')[1]),x=>x.charCodeAt(0));const transfer=new DataTransfer();transfer.items.add(new File([bytes],'粘贴图片.png',{type:'image/png'}));const input=document.querySelector('textarea[aria-label="消息输入"]');input.focus();input.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:transfer}));})()`);
  for(let count=1;count<=3;count++){await paste();await until(`document.querySelectorAll('.composer .image-attachment img').length===${count}`);}
  const cid=(await read("window.cowork.call('conversation.list')")).conversations?.[0]?.id;
  // Obtain the durable draft through the same API used when reopening history.
  const list=await read("window.cowork.call('conversation.list')");
  const id=cid || (Array.isArray(list)?list[0]?.id:list.items?.[0]?.id);
  assert.ok(id,JSON.stringify(list));
  let conversation;
  for(let i=0;i<80;i++){conversation=await read(`window.cowork.call('conversation.get',{conversation_id:${JSON.stringify(id)}})`);if(conversation.draft.attachments.length===3)break;await delay(50);}
  assert.equal(conversation.draft.attachments.length,3);imagePath=conversation.draft.attachments[0].path;
  await read("document.querySelector('textarea[aria-label=\"消息输入\"]').focus()");
  await window.webContents.insertText('请查看这些图片，并说明图表内容。');
  await read(`window.cowork.call('settings.select_model',{model_id:${JSON.stringify(settings.selected_model_id)}})`);
  await read("document.querySelector('.send-button').click()");
  await until("document.querySelectorAll('.message.user .image-attachment img').length===3&&document.body.textContent.includes('已查看图片，图表内容已保留')");
  await until(`window.cowork.call('conversation.get',{conversation_id:${JSON.stringify(id)}}).then(value=>value.run?.status==='completed')`);
  await delay(400);
  const positions=await read("[...document.querySelectorAll('.message.user .image-attachment')].map(e=>Math.round(e.getBoundingClientRect().top))");
  assert.equal(new Set(positions).size,1,'three attachments share a row');
  assert.ok(requests.some(payload=>payload.messages.some(message=>Array.isArray(message.content)&&message.content.filter(part=>part.type==='image_url').length===3)),'real vision request contains all three images');
  await read("(()=>{const b=document.querySelector('.process-summary');if(b?.getAttribute('aria-expanded')!=='true')b?.click()})()");
  await until("!!document.querySelector('.viewed-images .process-tool')");
  await read("document.querySelector('.viewed-images .process-tool').click()");
  await read("document.querySelector('.viewed-images').scrollIntoView({block:'center'})");
  await until("document.querySelector('.viewed-images img')?.naturalWidth>0");
  const thumb=await read("document.querySelector('.viewed-images img').src");assert.match(thumb,/cowork-local:/);
  // Reopening uses persisted tool results and keeps the image in the process timeline.
  await new Promise(resolve=>{window.webContents.once('did-finish-load',resolve);window.webContents.reload();});
  await until("!!document.querySelector('.process-summary')");
  await read("(()=>{const b=document.querySelector('.process-summary');if(b.getAttribute('aria-expanded')!=='true')b.click()})()");
  await until("!!document.querySelector('.viewed-images .process-tool')");
  await read("(()=>{const b=document.querySelector('.viewed-images .process-tool');if(b.getAttribute('aria-expanded')!=='true')b.click();document.querySelector('.viewed-images').scrollIntoView({block:'center'})})()");
  await until("document.querySelector('.viewed-images img')?.naturalWidth>0");
  if(process.env.COWORK_ATTACHMENT_SCREENSHOTS){await delay(300);await fs.mkdir(process.env.COWORK_ATTACHMENT_SCREENSHOTS,{recursive:true});await fs.writeFile(path.join(process.env.COWORK_ATTACHMENT_SCREENSHOTS,'conversation-images.png'),(await window.webContents.capturePage()).toPNG());}
  window.setSize(1100,850);window.webContents.setZoomFactor(1.25);
  await read("window.cowork.call('settings.theme',{theme:'dark'})");await delay(300);
  assert.ok(await read('document.documentElement.scrollWidth<=innerWidth+1'),'narrow layout has no page overflow');
  const wrapped=await read("[...document.querySelectorAll('.message.user .image-attachment')].map(e=>Math.round(e.getBoundingClientRect().top))");
  assert.ok(new Set(wrapped).size>1,'narrow layout wraps thumbnails');
  if(process.env.COWORK_ATTACHMENT_SCREENSHOTS)await fs.writeFile(path.join(process.env.COWORK_ATTACHMENT_SCREENSHOTS,'conversation-images-dark.png'),(await window.webContents.capturePage()).toPNG());
  await read("document.querySelector('.viewed-images .image-attachment-open').click()");
  await until("!!document.querySelector('.workspace img')");
  console.log('ATTACHMENTS_FIXTURE_OK',JSON.stringify({platform:process.platform,scale:require('electron').screen.getPrimaryDisplay().scaleFactor,zoom:window.webContents.getZoomFactor(),size:window.getSize()}));
}).then(async()=>{clearTimeout(deadline);server.close();await window.webContents.executeJavaScript("window.cowork.call('app.quit').catch(()=>{});null");}).catch(async error=>{console.error(error);clearTimeout(deadline);server?.close();process.exitCode=1;try{await window?.webContents.executeJavaScript("window.cowork.call('app.quit')");}catch{}app.exit(1);});
