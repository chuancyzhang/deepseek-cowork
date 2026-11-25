const {app,BrowserWindow,protocol,net,ipcMain}=require('electron');
const path=require('node:path'),fs=require('node:fs/promises'),assert=require('node:assert/strict');
const {FileHost}=require('../electron/files.cjs');
const data=process.env.COWORK_MEDIA_TEST_DATA,host=new FileHost();
app.setPath('userData',path.join(data,'profile'));
// Read the production scheme privileges so this regression also checks streaming configuration.
const source=require('node:fs').readFileSync(path.join(__dirname,'../electron/main.cjs'),'utf8');
const match=source.match(/scheme: 'cowork-local', privileges: (\{[^}]+\})/);
protocol.registerSchemesAsPrivileged([{scheme:'cowork-local',privileges:Function('return ('+match[1]+')')()}]);
let window;const errors=[];
const filesByPath=new Map(),actions=[];
const deadline=setTimeout(()=>{console.error('media preview timeout',errors);app.exit(1);},25000);
app.whenReady().then(async()=>{
  protocol.handle('cowork-local',request=>host.response(request,net.fetch));
  ipcMain.handle('cowork:call',async (_event,method,params)=>{
    if(method==='media.release_preview')return host.release(params.url);
    if(method==='fixture.preview_open')return host.files.has(new URL(params.url).pathname.split('/')[1]);
    assert.equal(params.conversation_id,'one');
    if(method==='file.open')return filesByPath.get(params.path);
    if(method==='media.preview'){
      try{await fs.access(params.path);}catch(error){if(error.code==='ENOENT')return{missing:true};throw error;}
      return host.register(filesByPath.get(params.path),true);
    }
    if(method==='media.retry'||method==='media.resume'){actions.push({method,...params});return;}
    throw Error(method);
  });
  window=new BrowserWindow({show:false,width:1000,height:750,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,
    backgroundThrottling:false,preload:path.join(__dirname,'../electron/preload.cjs')}});
  window.webContents.on('console-message',details=>{if(details.level==='error')errors.push(details.message);});
  const read=code=>window.webContents.executeJavaScript(code);
  const until=async(code)=>{const start=Date.now();while(Date.now()-start<8000){if(await read(code))return;await new Promise(resolve=>setTimeout(resolve,30));}throw Error('Timed out: '+code+' '+errors.join('\n')+' '+await read("JSON.stringify({text:document.body.innerText,media:[...document.querySelectorAll('audio,video')].map(item=>({src:item.src,ready:item.readyState,error:item.error?.message}))})"));};
  const load=async()=>{await window.loadFile(path.join(data,'dist/index.html'));await until('!!window.deliver');};
  await load();
  const wav=Buffer.alloc(44+16000*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(16000,24);wav.writeUInt32LE(32000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
  await fs.writeFile(path.join(data,'sample.wav'),wav);
  const video=await read(`new Promise((resolve,reject)=>{
    const canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;
    const context=canvas.getContext('2d'),stream=canvas.captureStream(0),mp4=MediaRecorder.isTypeSupported('video/mp4;codecs=avc1.42001E');
    const mimeType=mp4?'video/mp4;codecs=avc1.42001E':'video/webm;codecs=vp8',recorder=new MediaRecorder(stream,{mimeType}),parts=[];
    recorder.ondataavailable=event=>parts.push(event.data);recorder.onerror=reject;
    recorder.onstop=async()=>{stream.getTracks().forEach(track=>track.stop());const bytes=new Uint8Array(await new Blob(parts).arrayBuffer());resolve({name:mp4?'日落短片.mp4':'日落短片.webm',base64:btoa(Array.from(bytes,byte=>String.fromCharCode(byte)).join(''))});};
    const sky=context.createLinearGradient(0,0,0,220);sky.addColorStop(0,'#273959');sky.addColorStop(1,'#ffc075');
    const sea=context.createLinearGradient(0,180,0,360);sea.addColorStop(0,'#53778c');sea.addColorStop(1,'#233855');
    let frame=0;
    const draw=()=>{context.fillStyle=sky;context.fillRect(0,0,640,210);context.fillStyle='#ffe2a0';context.beginPath();context.arc(455,165,45,0,Math.PI*2);context.fill();
      context.fillStyle=sea;context.fillRect(0,210,640,150);context.strokeStyle='#edba8c';
      for(let line=0;line<10;line++){const offset=(line*31+frame*4)%75;context.beginPath();context.moveTo(455-offset,222+line*12);context.lineTo(455+offset,222+line*12);context.stroke();}
      stream.getVideoTracks()[0].requestFrame();};
    recorder.start();draw();const timer=setInterval(()=>{draw();if(++frame===20){clearInterval(timer);recorder.stop();}},80);
  })`);
  const encoded=Buffer.from(video.base64,'base64');assert.ok(encoded.length>400,'video fixture contains encoded frames');await fs.writeFile(path.join(data,video.name),encoded);
  const files=[['sample.wav','audio'],[video.name,'video']].map(([name,kind])=>host.register({id:name,path:path.join(data,name),name,payload:{kind:'preview',preview_kind:kind}}));
  for(const file of files)filesByPath.set(file.path,file);
  for(const reload of [false,true]){
    if(reload)await load();
    await read(`window.deliver(${JSON.stringify(files)})`);
    await until("document.querySelectorAll('.message-media audio,.message-media video').length===2&&[...document.querySelectorAll('.message-media audio,.message-media video')].every(item=>item.readyState>=1&&!item.error)");
    await read("document.querySelector('.message-media audio').currentTime=.5");
    await until("document.querySelector('.message-media audio').currentTime>=.49");
    await read("(()=>{const item=document.querySelector('.message-media video');item.muted=true;return item.play();})()");
    await until("document.querySelector('.message-media video').currentTime>0");
    await read("document.querySelector('.message-media video').pause();document.querySelectorAll('.message-media figcaption button')[1].click()");
    await until("document.querySelector('.file-media-preview video')?.readyState>=1");
  }
  await load();
  const first=files[1],second={...first,id:'second',path:path.join(data,'second',first.name)};
  await fs.mkdir(path.dirname(second.path));await fs.writeFile(second.path,encoded);filesByPath.set(second.path,second);
  const task={conversation_id:'one',run_id:'run',capability:'video_generate',created_at:1,updated_at:1,can_resume:false,can_retry:false,can_redownload:true};
  const taskUpdate=(id,file,value)=>read('window.deliverTask('+JSON.stringify({...task,id,tool_call_id:id,files:[file.path],...value})+')');
  await taskUpdate('second',second,{status:'running',phase:'queued',revision:1});
  await taskUpdate('first',first,{status:'completed',revision:1});
  await read('window.deliver('+JSON.stringify([...files,second])+');window.showVideoConversation();');
  await until('document.querySelector("aside [data-video-task=first]") && document.querySelector(".messages [data-video-task=second] [role=status]")?.textContent==="服务排队中"');
  await read('window.showEarlier(true)');
  await until('document.querySelector(".messages [data-video-task=first]") && !document.querySelector("aside [data-video-task]")');
  assert.deepEqual(await read('[...document.querySelectorAll(".messages [data-video-task]")].map(card=>card.dataset.videoTask)'),['first','second']);
  assert.match(await read('document.getElementById("message-normal-reply").textContent'),/可以沿江拍摄/);
  assert.equal(await read('document.querySelectorAll("video").length'),0);
  if(process.env.COWORK_MEDIA_TEST_SCREENSHOTS){
    const screenshots=process.env.COWORK_MEDIA_TEST_SCREENSHOTS;
    await fs.mkdir(screenshots,{recursive:true});
    window.showInactive();
    window.webContents.setZoomFactor(.85);
    await new Promise(resolve=>setTimeout(resolve,250));
    await fs.writeFile(path.join(screenshots,'video-task.png'),(await window.webContents.capturePage()).toPNG());
    window.webContents.setZoomFactor(1);
  }
  await read('document.querySelector("#message-first-reply .markdown button").click()');
  assert.equal(await read('window.linkedVideo'), 'first', 'the reply link targets its own video card');
  await until('document.querySelector("[data-video-task=first] [role=status]")?.textContent==="可播放"&&document.querySelector("[data-video-task=first] video")?.readyState>=1');
  assert.equal(await read('document.querySelectorAll("video").length'),1);
  await read('(()=>{const player=document.querySelector("[data-video-task=first] video");player.muted=true;return player.play();})()');
  await until('document.querySelector("[data-video-task=first] video").currentTime>0');
  const url=await read('document.querySelector("[data-video-task=first] video").src');
  await read('window.selectConversation("two")');await until('!document.querySelector("video,.chat-video-tasks")');
  await until('window.cowork.call("fixture.preview_open",{url:'+JSON.stringify(url)+'}).then(open=>!open)');
  await fs.rename(first.path,first.path+'.moved');
  await read('window.selectConversation("one")');
  await until('!!document.querySelector("[data-video-task=first] [aria-expanded=false]")');
  await read('document.querySelector("[data-video-task=first] [aria-expanded=false]").click()');
  await until('document.querySelector("[data-video-task=first] [role=status]")?.textContent==="本地成片找不到"');
  await read('[...document.querySelectorAll("[data-video-task=first] button")].find(button=>button.textContent==="重试下载").click()');
  await until('!!document.querySelector("[data-video-task=first] [aria-expanded=false]")');
  assert.deepEqual(actions.pop(),{method:'media.resume',id:'first',conversation_id:'one',missing_file:first.path});
  await fs.rename(first.path+'.moved',first.path);

  window.setSize(720,640);window.webContents.setZoomFactor(1.25);
  await read('document.documentElement.dataset.theme="dark"');
  await taskUpdate('second',second,{status:'unknown',message:'服务未确认接单',diagnostic:{stage:'submit',message:'HTTP 503'},revision:2});
  await until('document.querySelector("[data-video-task=second] [role=status]")?.textContent==="未确认接单"');
  assert.equal(await read('Boolean(document.querySelector("[data-video-task=second] button,[data-video-task=second] input"))'),false);
  assert.equal(await read('document.querySelector("[data-video-task=second] details").open'),false);
  await taskUpdate('second',second,{status:'not_submitted',message:'生成服务繁忙，需求已保留。',can_retry:true,revision:3});
  await until('document.querySelector("[data-video-task=second] button")?.textContent==="重试生成"');
  await read('document.querySelector("[data-video-task=second] button").click()');
  await until('document.querySelector("[data-video-task=second] button")?.textContent==="重试生成"');
  assert.deepEqual(actions,[{method:'media.retry',id:'second',conversation_id:'one',expected_revision:3}]);
  assert.ok(await read('document.documentElement.scrollWidth<=innerWidth+1'));
  assert.deepEqual(errors,[]);console.log('MEDIA_PREVIEW_FIXTURE_OK');
}).then(()=>{clearTimeout(deadline);window?.destroy();app.exit(0);}).catch(error=>{console.error(error);clearTimeout(deadline);window?.destroy();app.exit(1);});
