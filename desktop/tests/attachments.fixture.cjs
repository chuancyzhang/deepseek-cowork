const {app,BrowserWindow,Menu,clipboard,ClipboardItem,nativeImage}=require('electron');
const http=require('node:http'),fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const requests=[];let imagePath='',window,server;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const {Backend}=require('../electron/backend.cjs'),backendCall=Backend.prototype.call;
let sendWaiting=false;
Backend.prototype.call=async function(method,params){
  if(method==='conversation.send'){sendWaiting=true;await delay(600);}
  try{return await backendCall.call(this,method,params);}finally{if(method==='conversation.send')sendWaiting=false;}
};
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
  // Inspect the real native menu from actual right-clicks without leaving it open.
  let contextMenu;
  Menu.prototype.popup=function(){contextMenu=this;};
  const copyImage=async(selector,pasteBack=false)=>{
    await read(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
    await until(`document.querySelector(${JSON.stringify(selector)})?.naturalWidth>0`);
    await delay(100);
    const point=await read(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
    const scale=window.webContents.getZoomFactor(),position={x:Math.round(point.x*scale),y:Math.round(point.y*scale),button:'right',clickCount:1};
    contextMenu=null;window.webContents.sendInputEvent({type:'mouseDown',...position});window.webContents.sendInputEvent({type:'mouseUp',...position});
    for(let i=0;i<100&&!contextMenu;i++)await delay(20);
    const copy=contextMenu?.items.find(item=>item.label==='复制图片');assert.ok(copy?.enabled,'loaded image has a copy action');
    const previous=await Promise.all((await clipboard.read()).map(async item=>new ClipboardItem(Object.fromEntries(await Promise.all(item.types.map(async type=>[type,await item.getType(type)]))))));
    try {
      clipboard.clear();copy.click();
      for(let i=0;i<100&&!await clipboard.has('image/png');i++)await delay(20);
      const item=(await clipboard.read()).find(item=>item.types.includes('image/png'));
      assert.ok(item,'clipboard contains image data');
      const image=nativeImage.createFromBuffer(Buffer.from(await (await item.getType('image/png')).arrayBuffer()));
      assert.deepEqual(image.getSize(),{width:320,height:200},'copy retains original image dimensions');
      if(pasteBack){
        await read("document.querySelector('textarea[aria-label=\"消息输入\"]').focus()");window.webContents.paste();
        await until("document.querySelectorAll('.composer .image-attachment').length===4");
        await read("document.querySelectorAll('.composer .image-attachment-remove')[3].click()");
        await until("document.querySelectorAll('.composer .image-attachment').length===3");
      }
    } finally {await clipboard.write(previous);}
  };
  await until("!!window.cowork&&!!document.querySelector('textarea[aria-label=\"消息输入\"]')");
  const settings=await read(`window.cowork.call('settings.save_model',{name:'图片验收',model_name:'fixture',api_key:'local-only',base_url:'http://127.0.0.1:${server.address().port}/v1',supports_vision:true})`);
  await read("window.cowork.call('settings.permission',{enabled:true})");
  window.webContents.reload();
  await delay(300);
  await until("!!document.querySelector('textarea[aria-label=\"消息输入\"]')&&document.body.innerText.includes('图片验收')");
  const paste=()=>read(`(()=>{const canvas=document.createElement('canvas');canvas.width=320;canvas.height=200;const c=canvas.getContext('2d');c.fillStyle='#f5f5f5';c.fillRect(0,0,320,200);c.fillStyle='#404040';c.font='22px sans-serif';c.fillText('图片预览验收',40,65);c.fillRect(40,100,65,55);c.fillRect(125,120,65,35);c.fillRect(210,85,65,70);const bytes=Uint8Array.from(atob(canvas.toDataURL('image/png').split(',')[1]),x=>x.charCodeAt(0));const transfer=new DataTransfer();transfer.items.add(new File([bytes],'粘贴图片.png',{type:'image/png'}));const input=document.querySelector('textarea[aria-label="消息输入"]');input.focus();input.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:transfer}));})()`);
  for(let count=1;count<=3;count++){await paste();await until(`document.querySelectorAll('.composer .image-attachment img').length===${count}`);}
  await copyImage('.composer .image-attachment img',true);
  const cid=(await read("window.cowork.call('conversation.list')")).conversations?.[0]?.id;
  // Input recovery is local; reopening the renderer must preserve attachments.
  const list=await read("window.cowork.call('conversation.list')");
  const id=cid || (Array.isArray(list)?list[0]?.id:list.items?.[0]?.id);
  assert.ok(id,JSON.stringify(list));
  const input=await read(`JSON.parse(localStorage.getItem('cowork-pending-draft:'+${JSON.stringify(id)})).value`);
  assert.equal(input.attachments.length,3);imagePath=input.attachments[0].path;
  for(const name of ['image-review','summary-writing'])await read(`window.cowork.call('skills.enable',{name:${JSON.stringify(name)},enabled:true})`);
  await read(`window.cowork.call('conversation.preferences',{conversation_id:${JSON.stringify(id)},value:{selected_skill_names:['image-review']}})`);
  await until("!!document.querySelector('[aria-label=\"选择技能 image-review\"]')");
  await read("document.querySelector('[aria-label=\"选择技能 image-review\"]').click()");
  await until("document.querySelector('.composer-submenu strong')?.textContent==='技能'&&!!document.querySelector('.picker-row[aria-pressed=\"true\"]')");
  assert.equal(await read("document.querySelector('.picker-row[aria-pressed=\"true\"]').textContent"),'Image Review');
  await read("[...document.querySelectorAll('.composer-submenu .picker-row')].find(e=>e.textContent==='Summary Writing').click()");
  await until("document.querySelectorAll('.composer .skill-chip').length===2");
  if(process.env.COWORK_ATTACHMENT_SCREENSHOTS){await fs.mkdir(process.env.COWORK_ATTACHMENT_SCREENSHOTS,{recursive:true});await fs.writeFile(path.join(process.env.COWORK_ATTACHMENT_SCREENSHOTS,'skills-picker.png'),(await window.webContents.capturePage()).toPNG());}
  await read("[...document.querySelectorAll('.composer-submenu .picker-row')].find(e=>e.textContent==='Image Review').click()");
  await until("document.querySelectorAll('.composer .skill-chip').length===1&&!!document.querySelector('[aria-label=\"选择技能 summary-writing\"]')");
  await read("document.querySelector('[aria-label=\"移除能力 summary-writing\"]').click();document.querySelector('textarea[aria-label=\"消息输入\"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  await until("document.querySelectorAll('.composer .skill-chip').length===0");
  assert.equal(await read("document.querySelectorAll('.composer .image-attachment').length"),3,'changing skills preserves attachments');
  await read("document.querySelector('textarea[aria-label=\"消息输入\"]').focus()");
  await window.webContents.insertText('请查看这些图片，并说明图表内容。');
  await read(`window.cowork.call('settings.select_model',{model_id:${JSON.stringify(settings.selected_model_id)}})`);
  await read("document.querySelector('.send-button').click()");
  for(let i=0;i<100&&!sendWaiting;i++)await delay(5);
  assert.ok(sendWaiting,'the real submit is held to represent a slow machine');
  assert.equal(await read('document.body.textContent.includes("上一条消息的接收结果待确认")'),false,'normal pending sends do not show recovery');
  await until("document.querySelectorAll('.message.user .image-attachment img').length===3&&document.body.textContent.includes('已查看图片，图表内容已保留')");
  await until(`window.cowork.call('conversation.get',{conversation_id:${JSON.stringify(id)}}).then(value=>value.run?.status==='completed')`);
  await delay(400);
  const positions=await read("[...document.querySelectorAll('.message.user .image-attachment')].map(e=>Math.round(e.getBoundingClientRect().top))");
  assert.equal(new Set(positions).size,1,'three attachments share a row');
  await copyImage('.message.user .image-attachment img');
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
  await copyImage('.viewed-images img');
  await read("document.querySelector('.viewed-images .image-attachment-open').click()");
  await until("!!document.querySelector('.workspace img')");
  console.log('ATTACHMENTS_FIXTURE_OK',JSON.stringify({platform:process.platform,scale:require('electron').screen.getPrimaryDisplay().scaleFactor,zoom:window.webContents.getZoomFactor(),size:window.getSize()}));
}).then(async()=>{clearTimeout(deadline);server.close();await window.webContents.executeJavaScript("window.cowork.call('app.quit').catch(()=>{});null");}).catch(async error=>{console.error(error);if(process.env.COWORK_ATTACHMENT_SCREENSHOTS&&window){await fs.mkdir(process.env.COWORK_ATTACHMENT_SCREENSHOTS,{recursive:true});await fs.writeFile(path.join(process.env.COWORK_ATTACHMENT_SCREENSHOTS,'failure.png'),(await window.webContents.capturePage()).toPNG());console.error(await window.webContents.executeJavaScript('JSON.stringify({hidden:document.hidden,images:[...document.querySelectorAll(".image-attachment img")].map(el=>({complete:el.complete,width:el.naturalWidth,rect:el.getBoundingClientRect()}))})'));}clearTimeout(deadline);server?.close();process.exitCode=1;try{await window?.webContents.executeJavaScript("window.cowork.call('app.quit')");}catch{}app.exit(1);});
