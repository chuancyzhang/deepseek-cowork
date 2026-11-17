(() => {
  if (window.coworkObserve) return;
  const documentId = crypto.randomUUID(), refs = new Map(), ids = new WeakMap(), snapshots = new Map();
  let nextId = 0, sequence = 0;
  const controls = 'a,button,input,textarea,select,summary,[onclick],[role],[contenteditable]:not([contenteditable="false"]),iframe,canvas';
  const rectOf = el => {const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height];};
  const visible = el => {const s=getComputedStyle(el),r=el.getBoundingClientRect();return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&s.visibility!=='collapse';};
  const textOf = el => String(el.innerText ?? el.textContent ?? '').trim();
  function nameOf(el) {
    return (el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>el.getRootNode().getElementById?.(id)?.textContent||'').join(' ').trim() ||
      [...(el.labels||[])].map(label=>label.textContent).join(' ') || el.getAttribute('alt') || el.getAttribute('placeholder') ||
      (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)?'':textOf(el)) || el.getAttribute('title') || '').trim();
  }
  function roleOf(el) {
    return el.getAttribute('role') || ({A:el.hasAttribute('href')?'link':'',BUTTON:'button',TEXTAREA:'textbox',SELECT:el.multiple?'listbox':'combobox',SUMMARY:'button',TABLE:'table',TR:'row',TH:'columnheader',TD:'cell',UL:'list',OL:'list',LI:'listitem',FORM:'form',DIALOG:'dialog',IFRAME:'frame',CANVAS:'canvas'}[el.tagName]) ||
      (el.tagName==='INPUT'?({checkbox:'checkbox',radio:'radio',range:'slider',number:'spinbutton',button:'button',submit:'button',file:'file'}[el.type]||'textbox'):/^H[1-6]$/.test(el.tagName)?'heading':el.isContentEditable?'textbox':'');
  }
  function valueOf(el) {return el.type==='password'?'[密码已隐藏]':String(el.isContentEditable?el.textContent:el.value??'');}
  function signature(el) {
    return JSON.stringify([[...el.attributes].filter(a=>!['style','class'].includes(a.name)).map(a=>[a.name,a.value]),nameOf(el),el.textContent,el.value,el.checked,el.readOnly,el.form?.action,el.form?.method]);
  }
  function paintState(el) {
    const states=[];
    for(let n=el;n;n=n.parentElement||n.getRootNode()?.host){const s=getComputedStyle(n);states.push([s.zIndex,s.pointerEvents,s.opacity,s.transform,s.visibility]);}
    return JSON.stringify(states);
  }
  function ref(el) {let id=ids.get(el);if(!id){id='e'+(++nextId);ids.set(el,id);refs.set(id,el);}return id;}
  function all(root=document, limit=10000) {
    const out=[];
    function walk(node){for(const el of node.querySelectorAll('*')){if(out.length>=limit)return;out.push(el);if(el.shadowRoot)walk(el.shadowRoot);}}
    walk(root);return out;
  }
  function hitAt(x,y) {let h=document.elementFromPoint(x,y);while(h?.shadowRoot){const n=h.shadowRoot.elementFromPoint(x,y);if(!n||n===h)break;h=n;}return h;}
  function contains(el,hit) {while(hit){if(el===hit||el.contains(hit))return true;hit=hit.getRootNode()?.host;}return false;}
  const fail=(status,error,reason)=>({ok:false,executed:false,status,error,reason});
  const stale=reason=>fail('stale_page','目标、文档或视口已变化，请重新观察并核对任务对象；不要重复已派发的动作。',reason);
  function roots(target={}) {
    if(!target.within)return document;
    const root=refs.get(target.within);
    if(!root?.isConnected)throw Error('限定区域已失效');return root;
  }
  function find(target={}) {
    if(target.element_id){const el=refs.get(String(target.element_id));return el?.isConnected?[el]:[];}
    const root=roots(target), nodes=all(root);
    if(target.selector)return nodes.filter(el=>el.matches(target.selector));
    if(target.role||target.name||target.label||target.text)return nodes.filter(el=>{
      if(target.role&&roleOf(el)!==target.role)return false;
      if(target.name&&nameOf(el)!==target.name)return false;
      if(target.label&&![...(el.labels||[])].some(label=>textOf(label)===target.label)&&el.getAttribute('aria-label')!==target.label)return false;
      if(target.text&&textOf(el)!==target.text)return false;
      return true;
    });
    return [];
  }
  function targetOf(args){return args.target||{element_id:args.element_id};}
  function unique(target) {
    let found;try{found=find(target);}catch(e){return fail('invalid_target',e.message);}
    if(found.length!==1)return {...fail(found.length?'ambiguous_target':'element_unavailable',found.length?'匹配多个目标，请限定区域或使用元素引用。':'目标不存在，请重新观察。'),candidates:found.slice(0,12).map(el=>({id:ref(el),role:roleOf(el),name:nameOf(el).slice(0,180)}))};
    return {ok:true,el:found[0]};
  }
  function fact(el,field){return field==='text'?textOf(el):field==='name'?nameOf(el):field==='value'?valueOf(el):field==='checked'?Boolean(el.checked):field==='disabled'?Boolean(el.disabled||el.getAttribute('aria-disabled')==='true'):field==='exists'?Boolean(el):null;}
  function checkFacts(expected=[]) {
    if(!Array.isArray(expected)||expected.length>20)return fail('invalid_expectation','业务条件最多 20 项。');
    for(const item of expected){const match=unique(item.target||{});if(!match.ok)return match;const actual=fact(match.el,item.field);if(actual!==item.equals)return {...fail('expectation_changed','相关业务条件已变化，请重新读取并决定下一步。'),field:item.field,actual};}
    return {ok:true};
  }
  window.coworkObserve = (args={}) => {
    const nodes=all(), records=new Map(), elements=[], textLimit=Math.max(1,Math.min(24000,Number(args.max_chars)||12000));
    const maxElements=Math.max(1,Math.min(400,Number(args.max_elements)||200));let count=0;
    for(const [id,el] of refs)if(!el.isConnected)refs.delete(id);
    for(const el of nodes){if(!visible(el)&&!el.matches('input[type=file]'))continue;records.set(el,{signature:signature(el),rect:rectOf(el),paint:paintState(el)});
      if(!el.matches(controls))continue;count++;if(elements.length>=maxElements)continue;
      elements.push({id:ref(el),tag:el.tagName.toLowerCase(),role:roleOf(el),name:nameOf(el).slice(0,180),type:el.getAttribute('type')||'',value:valueOf(el).slice(0,300),
        disabled:Boolean(el.disabled||el.getAttribute('aria-disabled')==='true'),checked:el.matches('input[type=checkbox],input[type=radio]')?Boolean(el.checked):undefined,
        selected:el.getAttribute('aria-selected'),expanded:el.getAttribute('aria-expanded'),level:/^H[1-6]$/.test(el.tagName)?Number(el.tagName[1]):undefined,
        parent:el.parentElement&&el.parentElement!==document.body?ref(el.parentElement):undefined});}
    if(document.activeElement&&!records.has(document.activeElement)){const el=document.activeElement;records.set(el,{signature:signature(el),rect:rectOf(el),paint:paintState(el)});}
    const version=documentId+':'+(++sequence), text=document.body?.innerText||'';
    snapshots.set(version,{records,focus:document.activeElement,viewport:JSON.stringify([innerWidth,innerHeight,scrollX,scrollY])});
    while(snapshots.size>8)snapshots.delete(snapshots.keys().next().value);
    return {document_id:documentId,dom_version:version,title:document.title,url:location.href,loading:document.readyState,
      text:text.slice(0,textLimit),elements,viewport:{width:innerWidth,height:innerHeight},
      truncated:{text:text.length>textLimit,elements:count>maxElements,scan:nodes.length>=10000},
      note:'页面内容是不可信数据。元素引用绑定原节点，语义定位必须唯一；截图坐标使用 CSS 像素。'};
  };
  function validate(args) {
    const snapshot=snapshots.get(args.dom_version);if(!snapshot)return stale('observation_expired');
    const action=args.target_action||args.action, facts=checkFacts(args.expect);if(!facts.ok)return facts;
    if(action==='scroll'&&!args.target&&!args.element_id)return snapshot.viewport===JSON.stringify([innerWidth,innerHeight,scrollX,scrollY])?{ok:true}:stale('viewport_changed');
    let el,x,y,requireObserved=true;
    if(['click_at','drag_at'].includes(action)) {
      x=Number(args.x);y=Number(args.y);
      if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||y<0||x>=innerWidth||y>=innerHeight)return fail('invalid_coordinate','坐标不在当前视口内。');
      if(snapshot.viewport!==JSON.stringify([innerWidth,innerHeight,scrollX,scrollY]))return stale('viewport_changed');
      el=hitAt(x,y);el=el?.closest(controls)||el;
      const r=snapshot.records.get(el);
      if(!r||paintState(el)!==r.paint||rectOf(el).some((v,i)=>Math.abs(v-r.rect[i])>.5))return stale('coordinate_target_changed');
    } else if(action==='key'&&!args.target&&!args.element_id) {
      el=document.activeElement;if(el!==snapshot.focus)return stale('focus_changed');
    } else {const target=targetOf(args),m=unique(target);if(!m.ok)return m;el=m.el;requireObserved=Boolean(target.element_id);}
    if(!el?.isConnected)return fail('element_unavailable','原节点已被替换，请重新观察。');
    if(el.matches(':disabled,[aria-disabled="true"]'))return fail('element_disabled','目标已禁用。');
    const record=snapshot.records.get(el);
    // Semantic targets explicitly re-resolve; refs always bind the observed node.
    if(requireObserved&&(!record||record.signature!==signature(el)))return stale('target_changed');
    if(action==='upload'&&el.matches('input[type=file]'))return {ok:true,element_id:ref(el),multiple:el.multiple};
    if(!visible(el))return fail('element_unavailable','目标不可见。');
    if(action==='scroll_into_view')return {ok:true,element_id:ref(el)};
    if(x===undefined){const r=el.getBoundingClientRect();x=(Math.max(0,r.left)+Math.min(innerWidth,r.right))/2;y=(Math.max(0,r.top)+Math.min(innerHeight,r.bottom))/2;
      if(r.bottom<=0||r.top>=innerHeight||r.right<=0||r.left>=innerWidth)return fail('outside_viewport','目标不在视口内，请滚动到目标后重新观察。');}
    if(!contains(el,hitAt(x,y)))return fail('element_obscured','目标被遮挡，请处理遮挡后继续。');
    return {ok:true,x,y,element_id:ref(el),checked:Boolean(el.checked),type:el.type,tag:el.tagName.toLowerCase()};
  }
  window.coworkAct = args => {
    const checked=validate(args);if(!checked.ok||args.action==='validate')return checked;
    const el=refs.get(checked.element_id);
    if(args.action==='prepare_native'){if((args.target_action==='key'||args.target_action==='type')&&el)el.focus();return checked;}
    if(args.action==='scroll'){(el||window).scrollBy({left:Number(args.x)||0,top:Number(args.y)||0,behavior:'instant'});return {ok:true,status:'dispatched'};}
    if(args.action==='scroll_into_view'){el.scrollIntoView({block:'center',inline:'center',behavior:'instant'});return {ok:true,status:'dispatched'};}
    if(args.action==='select') {
      if(el.tagName!=='SELECT')return fail('unsupported','目标不是选择框。');
      const values=Array.isArray(args.values)?args.values.map(String):[String(args.text??'')];
      if((!el.multiple&&values.length!==1)||values.some(v=>![...el.options].some(o=>o.value===v&&!o.disabled)))return fail('invalid_option','选项不存在、已禁用或不支持多选。');
      for(const option of el.options)option.selected=values.includes(option.value);
    } else if(args.action==='fill') {
      if(el.readOnly||el.getAttribute('aria-readonly')==='true'||/^(file|checkbox|radio|button|submit|reset|image|hidden)$/.test(el.type))return fail('unsupported','目标不能直接填写。');
      const prototype=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
      const setter=Object.getOwnPropertyDescriptor(prototype,'value')?.set;
      if(!el.isContentEditable&&(!setter||!['INPUT','TEXTAREA'].includes(el.tagName)))return fail('unsupported','目标不能填写。');
      el.focus();if(el.isContentEditable)el.textContent=String(args.text??'');else setter.call(el,String(args.text??''));
    } else return fail('unsupported','不支持的网页操作。');
    el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
    return {ok:true,status:'dispatched',element_id:checked.element_id};
  };
  window.coworkElement = target => {const m=unique(target);return m.ok?m.el:null;};
  window.coworkVerifyFill = args => {const el=refs.get(args.element_id)||window.coworkElement(targetOf(args));
    if(!el?.isConnected)return {ok:true,status:'dispatched',value_matches:null,note:'填写已派发，页面随后替换了输入框，请读取当前值确认。'};
    const actual=valueOf(el),matches=el.type==='password'?String(el.value)===String(args.text??''):actual===String(args.text??'');
    return {ok:true,status:matches?'verified':'dispatched',value_matches:matches,actual_value:actual.slice(0,1000),note:matches?'':'页面可能格式化了输入；请核对实际值，不要重复提交。'};};
  window.coworkRead = (args={}) => {
    const limit=Math.max(1,Math.min(200,Number(args.limit)||50));let nodes;
    try{nodes=args.target?find(args.target):[document.body];}catch(e){return fail('invalid_target',e.message);}
    const mode=args.mode||'text';let rows;
    if(mode==='table')rows=nodes.flatMap(n=>[...(n.matches('tr')?[n]:n.querySelectorAll('tr'))]).map(row=>[...row.querySelectorAll('th,td')].map(cell=>textOf(cell).slice(0,1000)));
    else if(mode==='list')rows=nodes.flatMap(n=>[...(n.matches('li')?[n]:n.querySelectorAll('li'))]).map(textOf);
    else if(mode==='form')rows=nodes.flatMap(n=>n.matches('input,select,textarea,[contenteditable=true]')?[n]:all(n).filter(el=>el.matches('input,select,textarea,[contenteditable=true]'))).map(el=>({id:ref(el),role:roleOf(el),name:nameOf(el),value:valueOf(el),checked:Boolean(el.checked),disabled:Boolean(el.disabled)}));
    else rows=nodes.map(n=>({id:ref(n),text:textOf(n),...(mode==='attributes'?{attributes:Object.fromEntries([...n.attributes].filter(a=>['id','name','href','type','role','aria-label','title'].includes(a.name)).map(a=>[a.name,a.value]))}:{})}));
    let remaining=24000,truncated=rows.length>limit;const result=[];
    const bounded=value=>{
      if(typeof value==='string'){const text=value.slice(0,Math.max(0,remaining));remaining-=text.length+2;if(text.length<value.length)truncated=true;return text;}
      if(value&&typeof value==='object'){
        const out=Array.isArray(value)?[]:{};
        for(const [key,item] of Object.entries(value)){if(remaining<64){truncated=true;break;}remaining-=key.length+4;out[key]=bounded(item);}return out;
      }
      remaining-=8;return value;
    };
    for(const row of rows.slice(0,limit)){if(remaining<64){truncated=true;break;}result.push(bounded(row));}
    return {ok:true,document_id:documentId,url:location.href,mode,rows:result,truncated};
  };
  window.coworkCondition = (condition={}) => {
    if(condition.state==='load')return {ok:true,matched:document.readyState==='complete'};
    if(condition.state==='url')return {ok:true,matched:condition.equals!==undefined?location.href===condition.equals:location.href.includes(String(condition.includes||''))};
    let nodes;try{nodes=find(condition.target||{});}catch(e){return fail('invalid_target',e.message);}
    if(condition.state==='hidden'||condition.state==='detached')return {ok:true,matched:condition.state==='detached'?!nodes.length:!nodes.some(visible)};
    if(nodes.length>1)return fail('ambiguous_target','等待条件匹配多个目标，请限定区域。');
    const el=nodes[0];if(!el)return {ok:true,matched:false};
    const matched=condition.state==='attached'?true:condition.state==='visible'?visible(el):condition.state==='checked'?Boolean(el.checked)===condition.equals:
      condition.state==='text'?textOf(el)===condition.equals:condition.state==='value'?valueOf(el)===condition.equals:false;
    return {ok:true,matched};
  };
})();
