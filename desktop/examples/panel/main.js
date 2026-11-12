import {createCowork} from './client.js';
const host=createCowork(),text=document.getElementById('text'),status=document.getElementById('status');let state,change=Promise.resolve();
host.subscribe(value=>{state=value;document.getElementById('title').textContent=value.editor?.file.name||value.conversation?.title||'任务笔记';const content=value.editor?.text||value.state?.text||'';if(text.value!==content)text.value=content;document.getElementById('save').hidden=!value.editor;});
text.oninput=()=>{const value=text.value;change=change.then(()=>state.editor?host.editors.change(value):host.state.change({text:value})).then(()=>{status.textContent='输入已交给宿主，文件尚未正式保存';}).catch(error=>{status.textContent=error.message;});};
document.getElementById('save').onclick=async()=>{try{await change;await host.editors.save();status.textContent='文件已保存';}catch(error){status.textContent=error.message;}};
host.commands.register('example.task-notes.inspect',()=>host.navigation.go('chat'));
await host.ready();
