const {app,BrowserWindow,screen}=require('electron');
const path=require('node:path'),fs=require('node:fs/promises'),assert=require('node:assert/strict');
const data=process.env.COWORK_MERMAID_TEST_DATA;
app.setPath('userData',path.join(data,'profile'));
let window;const errors=[];
const deadline=setTimeout(()=>{console.error('Mermaid fixture timeout',errors);app.exit(1);},60000);
app.whenReady().then(async()=>{
  window=new BrowserWindow({show:false,width:1000,height:900,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  window.webContents.on('console-message',details=>{if(details.level==='error')errors.push(details.message);});
  const read=code=>window.webContents.executeJavaScript(code);
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const until=async(code)=>{for(let i=0;i<300;i++){if(await read(code))return;await delay(30);}throw Error('Timed out: '+code+' '+errors.join('\n')+' '+await read('document.body.innerText'));};
  const load=async()=>{await window.loadFile(path.join(data,'dist/index.html'));await until('!!window.deliver');};
  const deliver=(text,streaming=false,id='one')=>read(`window.deliver(${JSON.stringify({text,streaming,id})})`);
  const ready="[...document.querySelectorAll('.mermaid-viewport img')].filter(image=>image.complete&&image.naturalWidth>0).length";
  const click=label=>read(`Array.from(document.querySelectorAll('button')).find(b=>(b.getAttribute('aria-label')||b.textContent)===${JSON.stringify(label)}).click()`);
  const source='flowchart TB\n  User((用户))\n  subgraph Entry["入口层"]\n    Desktop["Electron 桌面应用"]\n    Schedule["常用任务 / 定时计划"]\n  end\n  subgraph UI["桌面界面层"]\n    React["React + TypeScript 工作台"]\n    Host["稳定宿主与 Host SDK"]\n  end\n  User --> Desktop --> React --> Host\n  Schedule --> Host\n';
  const markdown='```mermaid\n'+source+'```';
  await load();
  await deliver('```mermaid\nflowchart TB\n A[',true);
  await until("document.body.innerText.includes('图表代码正在生成')");
  assert.equal(await read(ready),0);
  await deliver(markdown,true);await until(ready+'===1');
  await click('复制源码');await until('!!window.copied');assert.equal(await read('window.copied'),source);
  await click('查看源码');await until("document.querySelector('.mermaid-diagram pre')?.textContent.includes('入口层')");
  await click('查看图表');await until(ready+'===1');
  await click('放大图表');
  await read("window.previousDiagram=document.querySelector('.mermaid-viewport img');null");
  await deliver(markdown+'\n\n后续说明',true);
  await delay(80);assert.ok(await read("window.previousDiagram===document.querySelector('.mermaid-viewport img')"));
  await click('适应宽度');
  const original=await read("document.querySelector('.mermaid-viewport img').src");
  await read("document.documentElement.style.cssText='--text:#eeeeee;--soft:#303030;--line:#777777;--bg:#202020'");
  await until("document.querySelector('.mermaid-viewport img').src!=="+JSON.stringify(original));
  await until(ready+'===1');
  await click('放大图表');await click('适应宽度');
  window.setSize(540,800);window.webContents.setZoomFactor(1.25);
  await delay(150);assert.ok(await read('document.documentElement.scrollWidth<=innerWidth+1'));
  // Cancelling a preview returns to the original theme, and source copying is unchanged.
  await read("document.documentElement.style.cssText=''");await delay(150);
  await deliver('```mermaid\nflowchart TB\n A[\n```');
  await until("!!document.querySelector('.mermaid-diagram [role=alert]')");
  assert.ok(await read("document.querySelector('.mermaid-diagram pre').textContent.includes('A[')"));
  await deliver('```python\nflowchart TB\n```');await until("!!document.querySelector('.code-block')");
  assert.equal(await read("document.querySelectorAll('.mermaid-diagram').length"),0);
  await deliver('```\n'+source+'```');await until(ready+'===1');
  // Late async output from another conversation must never replace the selected content.
  await deliver(markdown,false,'old');await deliver('另一个会话',false,'new');
  await delay(200);assert.equal(await read("document.querySelectorAll('.mermaid-diagram').length"),0);
  await load();await deliver(markdown);await until(ready+'===1');
  window.setSize(1000,900);window.webContents.setZoomFactor(1);
  if(process.env.COWORK_MERMAID_SCREENSHOTS){await delay(200);await fs.mkdir(process.env.COWORK_MERMAID_SCREENSHOTS,{recursive:true});await fs.writeFile(path.join(process.env.COWORK_MERMAID_SCREENSHOTS,'mermaid-diagram.png'),(await window.webContents.capturePage()).toPNG());}
  assert.deepEqual(errors,[]);
  // The application CSP also blocks events in Mermaid's temporary measuring DOM.
  await read('window.alert=()=>{window.unsafeEvent=true};null');
  // Hostile HTML stays inert; the final SVG is displayed as an isolated image.
  await deliver('```mermaid\nflowchart LR\n A["<img src=x onerror=alert(1)>"] --> B[完成]\n```');await until(ready+'===1');
  assert.equal(await read("document.querySelectorAll('.mermaid-diagram svg,.mermaid-diagram script,.mermaid-diagram img:not([src^=\"data:image/svg+xml\"])').length"),0);
  assert.equal(await read("document.querySelectorAll('[id^=cowork-mermaid-]').length"),0,'temporary rendering nodes are removed');
  assert.equal(await read('Boolean(window.unsafeEvent)'),false);
  assert.ok(errors.every(message=>message.includes('Content Security Policy')&&message.includes('blocked')),errors.join('\n'));
  console.log('MERMAID_FIXTURE_OK',JSON.stringify({platform:process.platform,scale:screen.getPrimaryDisplay().scaleFactor,zoom:window.webContents.getZoomFactor(),size:window.getSize()}));
}).then(()=>{clearTimeout(deadline);window?.destroy();app.exit(0);}).catch(error=>{console.error(error);clearTimeout(deadline);window?.destroy();app.exit(1);});
