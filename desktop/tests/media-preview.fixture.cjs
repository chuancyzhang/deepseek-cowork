const {app,BrowserWindow,protocol,net}=require('electron');
const path=require('node:path'),fs=require('node:fs/promises'),assert=require('node:assert/strict');
const {FileHost}=require('../electron/files.cjs');
const data=process.env.COWORK_MEDIA_TEST_DATA,host=new FileHost();
app.setPath('userData',path.join(data,'profile'));
// Read the production scheme privileges so this regression also checks streaming configuration.
const source=require('node:fs').readFileSync(path.join(__dirname,'../electron/main.cjs'),'utf8');
const match=source.match(/scheme: 'cowork-local', privileges: (\{[^}]+\})/);
protocol.registerSchemesAsPrivileged([{scheme:'cowork-local',privileges:Function('return ('+match[1]+')')()}]);
let window;const errors=[];
const deadline=setTimeout(()=>{console.error('media preview timeout',errors);app.exit(1);},25000);
app.whenReady().then(async()=>{
  protocol.handle('cowork-local',request=>host.response(request,net.fetch));
  window=new BrowserWindow({show:false,width:1000,height:750,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  window.webContents.on('console-message',details=>{if(details.level==='error')errors.push(details.message);});
  const read=code=>window.webContents.executeJavaScript(code);
  const until=async(code)=>{const start=Date.now();while(Date.now()-start<8000){if(await read(code))return;await new Promise(resolve=>setTimeout(resolve,30));}throw Error('Timed out: '+code+' '+errors.join('\n')+' '+await read("JSON.stringify({text:document.body.innerText,media:[...document.querySelectorAll('audio,video')].map(item=>({src:item.src,ready:item.readyState,error:item.error?.message}))})"));};
  const load=async()=>{await window.loadFile(path.join(data,'dist/index.html'));await until('!!window.deliver');};
  await load();
  const wav=Buffer.alloc(44+16000*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(16000,24);wav.writeUInt32LE(32000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
  await fs.writeFile(path.join(data,'sample.wav'),wav);
  const video=await read(`new Promise((resolve,reject)=>{const canvas=document.createElement('canvas');canvas.width=160;canvas.height=90;const context=canvas.getContext('2d'),stream=canvas.captureStream(0),recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'}),parts=[];recorder.ondataavailable=event=>parts.push(event.data);recorder.onerror=reject;recorder.onstop=async()=>{stream.getTracks().forEach(track=>track.stop());const bytes=new Uint8Array(await new Blob(parts).arrayBuffer());resolve(btoa(String.fromCharCode(...bytes)));};context.fillStyle='#aaaaaa';context.fillRect(0,0,160,90);recorder.start();let frame=0;const timer=setInterval(()=>{context.fillStyle=frame%2?'#303030':'#aaaaaa';context.fillRect(0,0,160,90);stream.getVideoTracks()[0].requestFrame();if(++frame===20){clearInterval(timer);recorder.stop();}},80);})`);
  assert.ok(Buffer.from(video,'base64').length>400,'video fixture contains encoded frames');await fs.writeFile(path.join(data,'sample.webm'),Buffer.from(video,'base64'));
  const files=[['sample.wav','audio'],['sample.webm','video']].map(([name,kind])=>host.register({id:name,path:path.join(data,name),name,payload:{kind:'preview',preview_kind:kind}}));
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
  assert.deepEqual(errors,[]);console.log('MEDIA_PREVIEW_FIXTURE_OK');
}).then(()=>{clearTimeout(deadline);window?.destroy();app.exit(0);}).catch(error=>{console.error(error);clearTimeout(deadline);window?.destroy();app.exit(1);});
