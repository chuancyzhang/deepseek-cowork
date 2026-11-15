const {app,BrowserWindow,protocol,ipcMain,screen}=require('electron');
const {SnippetHost}=require('../electron/snippets.cjs');
const path=require('node:path'),fs=require('node:fs/promises'),assert=require('node:assert/strict');
const data=process.env.COWORK_SNIPPET_TEST_DATA,diagnostics=[];
app.setPath('userData',path.join(data,'profile'));
protocol.registerSchemesAsPrivileged([require('../electron/plugins.cjs').PLUGIN_SCHEME]);
let window,copied='',delayCreate=false;
const host=new SnippetHost({diagnostic:(event,details)=>diagnostics.push({event,...details})});
const deadline=setTimeout(()=>{console.error('snippet fixture timeout');app.exit(1);},80000);
app.whenReady().then(async()=>{
  protocol.handle('cowork-plugin',request=>host.response(new URL(request.url)));
  window=new BrowserWindow({show:false,width:1000,height:800,webPreferences:{preload:path.join(data,'preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  host.attach(window.webContents);window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  ipcMain.handle('test-call',async(event,method,params)=>{
    assert.equal(event.senderFrame,window.webContents.mainFrame);
    if(method==='clipboard.write'){copied=params.text;return;}
    if(method==='snippet.create'){const result=host.create(event.sender,params);if(delayCreate)await new Promise(resolve=>setTimeout(resolve,200));return result;}
    if(method==='snippet.start')return host.start(event.sender,params.token);
    if(method==='snippet.release')return host.release(event.sender,params.token);
    if(method==='snippet.event')return host.event(event.sender,params);
    throw Error(method);
  });
  const read=code=>window.webContents.executeJavaScript(code),delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const until=async(code)=>{for(let i=0;i<420;i++){if(await read(code))return;await delay(35);}throw Error('Timed out: '+code+'\n'+await read('document.body.innerText'));};
  const deliver=(text,streaming=false,id='chat:message')=>read(`window.deliver(${JSON.stringify({text,streaming,id})});new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
  const click=label=>read(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent===${JSON.stringify(label)}).click()`);
  const child=()=>window.webContents.mainFrame.frames[0];
  const html='<div id="card"><h2>欢迎使用</h2><p>这是一个 HTML 片段。</p><button onclick="this.textContent=\'你好！\';alert(\'你好！\')">点击我</button></div>';
  const markdown='```html\n'+html+'\n```';
  await window.loadFile(path.join(data,'dist/index.html'));await until('!!window.deliver');
  await deliver('```html\n<button',true);await until("!!document.querySelector('.snippet-preview')");
  assert.ok(await read("[...document.querySelectorAll('button')].find(b=>b.textContent==='预览').disabled"));
  await deliver('```html\n<button',false);await delay(60);assert.equal(host.pages.size,0);
  await deliver(markdown,true);await until("![...document.querySelectorAll('button')].find(b=>b.textContent==='预览').disabled");
  assert.equal(host.pages.size,0,'closed streaming blocks do not autorun');
  await click('预览');await until("document.body.innerText.includes('预览已就绪')");
  assert.notEqual(child().processId,window.webContents.mainFrame.processId);
  assert.deepEqual(await child().executeJavaScript('[typeof require,typeof cowork,typeof process]'),['undefined','undefined','undefined']);
  assert.equal(await child().executeJavaScript('(()=>{try{return parent.document.body.innerText}catch{return "isolated"}})()'),'isolated');
  await child().executeJavaScript('document.querySelector("button").click()');await until("document.body.innerText.includes('提示： 你好！')");
  await click('代码');await click('预览');assert.equal(await child().executeJavaScript('document.querySelector("button").textContent'),'你好！');
  const token=[...host.pages.keys()][0];await deliver(markdown+'\n后续正文',true);await delay(70);assert.equal([...host.pages.keys()][0],token);
  await click('复制');await delay(40);assert.equal(copied,html+'\n');
  await click('重新运行');await until("document.body.innerText.includes('预览已就绪')");assert.equal(await child().executeJavaScript('document.querySelector("button").textContent'),'点击我');
  // A navigation cannot escape the preview's CSP or reach app resources.
  await child().executeJavaScript('location.href="https://example.com/"');await until("document.body.innerText.includes('不支持跳转')");assert.ok(!child()||child().url.startsWith('cowork-plugin://snippet-'));
  await deliver('```js\nconsole.log("计算结果",[1,2,3].map(x=>x*2));document.body.innerHTML="<b>JS 页面</b>";\n```');
  await until("document.querySelector('.snippet-toolbar>span').textContent==='JavaScript'");await delay(70);assert.equal(host.pages.size,0);
  await click('运行 / 输出');await until("document.body.innerText.includes('计算结果 [2,4,6]')");assert.equal(await child().executeJavaScript('document.body.textContent'),'JS 页面');
  await deliver('```js\nthrow Error("sample failure")\n```');await click('运行 / 输出');await until("document.body.innerText.includes('sample failure')");
  await click('代码');assert.ok(await read("document.querySelector('.snippet-source').textContent.includes('sample failure')"));
  await deliver('```html\n<!doctype html><html lang="zh"><head><style>body{padding:13px}</style></head><body><p id="full">完整页面</p><script>console.log("full document")</script></body></html>\n```');
  await click('预览');await until("document.body.innerText.includes('预览已就绪')");assert.equal(await child().executeJavaScript('document.querySelector("#full").textContent'),'完整页面');
  assert.equal(await child().executeJavaScript('getComputedStyle(document.body).padding'),'13px');
  await child().executeJavaScript('fetch("https://example.com/").catch(()=>{})');await until("document.body.innerText.includes('connect-src')");
  await deliver('```jsx\n<div/>\n```');await until("document.body.innerText.includes('额外运行环境')");assert.equal(await read("[...document.querySelectorAll('button')].some(b=>b.textContent==='预览')"),false);
  // Source changes, another chat and late creation responses cannot resurrect a run.
  await deliver(markdown);await until("document.querySelector('.snippet-toolbar>span').textContent==='HTML'");delayCreate=true;await click('预览');await deliver(markdown,false,'another-chat');await delay(400);assert.equal(host.pages.size,0);delayCreate=false;
  await click('预览');await until("document.body.innerText.includes('预览已就绪')");
  await read("document.documentElement.dataset.theme='dark';document.documentElement.style.cssText='--text:#ededed;--bg:#242424;--soft:#303030;--line:#555' ");await delay(100);
  assert.equal(await child().executeJavaScript('getComputedStyle(document.documentElement).color'),'rgb(237, 237, 237)');
  await read("document.documentElement.dataset.theme='light';document.documentElement.style.cssText=''");await delay(100);
  window.setSize(540,800);window.webContents.setZoomFactor(1.25);await delay(100);assert.ok(await read('document.documentElement.scrollWidth<=innerWidth+1'));
  window.setSize(1000,800);window.webContents.setZoomFactor(1);
  if(process.env.COWORK_SNIPPET_SCREENSHOTS){await delay(400);await fs.mkdir(process.env.COWORK_SNIPPET_SCREENSHOTS,{recursive:true});const rect=await read('({x:0,y:0,width:innerWidth,height:Math.min(innerHeight,Math.ceil(document.querySelector("main").getBoundingClientRect().bottom+80))})');await fs.writeFile(path.join(process.env.COWORK_SNIPPET_SCREENSHOTS,'code-preview.png'),(await window.webContents.capturePage(rect)).toPNG());}
  // The independently executing iframe can be removed even during a synchronous loop.
  await deliver('```js\nwhile(true){}\n```');await click('运行 / 输出');await until("document.body.innerText.includes('超过 10 秒未响应')");assert.equal(host.pages.size,0);assert.equal(window.webContents.isDestroyed(),false);
  await deliver(markdown);await click('预览');await until("document.body.innerText.includes('预览已就绪')");await click('停止预览');await delay(60);assert.equal(host.pages.size,0);
  await deliver('```js\nconsole.log("x".repeat(5000));for(let i=0;i<105;i++)console.log(i);\n```');await click('运行 / 输出');await until("document.body.innerText.includes('100 条上限')");assert.ok(await read("document.querySelector('.snippet-console').textContent.includes('已截断')"));
  // Two frames in the same message must not accept one another's events.
  await deliver(markdown+'\n\n'+markdown);await read("document.querySelectorAll('.snippet-toolbar')[0].querySelectorAll('button')[1].click();document.querySelectorAll('.snippet-toolbar')[1].querySelectorAll('button')[1].click()");
  await until("[...document.querySelectorAll('.snippet-footer')].filter(e=>e.textContent.includes('预览已就绪')).length===2");assert.equal(host.pages.size,2);
  await read("window.postMessage({type:'error',token:document.querySelector('iframe').src.split('snippet-')[1].replace('/',''),message:'forged error'},'*')");await delay(40);assert.ok(!(await read('document.body.innerText')).includes('forged error'));
  await window.webContents.mainFrame.frames[0].executeJavaScript('document.querySelector("button").click()');await until("document.querySelectorAll('.snippet-console').length===1");assert.equal(await window.webContents.mainFrame.frames[1].executeJavaScript('document.querySelector("button").textContent'),'点击我');
  await window.webContents.reload();await until('!!window.deliver');await deliver(markdown);await delay(100);assert.equal(host.pages.size,0,'history does not autorun');
  assert.ok(diagnostics.some(record=>record.event==='snippet_timeout'));
  assert.ok(diagnostics.some(record=>record.event==='snippet_error'&&record.reason==='runtime'));
  console.log('SNIPPETS_FIXTURE_OK',JSON.stringify({platform:process.platform,scale:screen.getPrimaryDisplay().scaleFactor,zoom:window.webContents.getZoomFactor(),size:window.getSize()}));
}).then(()=>{clearTimeout(deadline);window?.destroy();app.exit(0);}).catch(error=>{console.error(error);clearTimeout(deadline);window?.destroy();app.exit(1);});
