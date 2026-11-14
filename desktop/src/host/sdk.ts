import type {Attachment,Conversation,FileSession,Plugin,Settings} from '../types';
import type {HostStore} from './store';
import type {WorkbenchController} from './workbench';
import type {PluginRegistry} from '../plugins/registry';
import {conversationAt} from './conversations.ts';
export type PluginScope={conversationId?:string;filePath?:string;tabId?:string;extensionId:string};
export type Lease={active:boolean;mode:'active'|'warm'|'preview';plugin:Plugin;scope:PluginScope;bounds?:()=>DOMRect|undefined};
export const managementReads=['settings.get','settings.page','settings.channels','settings.general','skills.inventory','skills.get','skills.read','mcp.list','variables.list','experts.list','favorites.list','favorites.history','library.status','library.catalog','library.children','library.read','library.search','library.uploads','team.list','team.detail','team.collection','team.conversation','team.object','enterprise.status','enterprise.providers','jobs.list','jobs.get','themes.list','themes.preview','themes.resolve','themes.fonts','skillhub.search','skillhub.detail'] as const;
export const managementWrites=['settings.select_model','settings.permission','settings.context','settings.theme','settings.page_save','settings.action','settings.save_model','settings.delete_model','settings.channel_save','settings.import','models.discover','models.test','models.account_open','models.account_close','models.account_login','models.account_verify','models.account_keys','models.account_create_key','models.account_reconcile','models.account_discover','models.account_save','im.verify','skills.configure','skills.delete','skills.enable','skills.export','skills.import','skills.write','mcp.save','mcp.delete','mcp.test','variables.save','variables.delete','variables.undo','experts.save','experts.delete','favorites.save','favorites.delete','favorites.run','library.authorize','library.check_upload','library.confirm','library.login','library.logout','library.reference','library.select','library.tenant','library.upload','team.download','team.upload','team.execute','enterprise.bind','enterprise.login','enterprise.logout','enterprise.refresh','jobs.cancel','themes.activate','themes.appearance','themes.commit_preview','themes.discard_preview','themes.delete','themes.export','themes.import','skillhub.install','skillhub.refresh'] as const;
const readMethods:Record<string,string>={'conversations.list':'conversations.read','conversations.get':'conversations.read','conversations.search':'conversations.read','drafts.get':'drafts.read','editors.get':'document.read','files.list':'files.read','management.read':'management.read','state.get':'navigation','desktop.tabs':'desktop'};
const writeMethods:Record<string,string>={'conversations.create':'conversations.write','conversations.select':'navigation','conversations.rename':'conversations.write','conversations.archive':'conversations.write','conversations.delete':'conversations.write','conversations.branch':'conversations.write','conversations.preferences':'conversations.write','runs.send':'runs.control','runs.stop':'runs.control','runs.respond':'runs.control','drafts.change':'drafts.write','drafts.save':'drafts.write','editors.open':'files.read','editors.change':'document.edit','editors.save':'document.edit','files.create':'files.write','navigation.go':'navigation','theme.set':'theme','dialogs.confirm':'dialogs','desktop.open':'desktop','desktop.close':'desktop','desktop.input':'desktop','desktop.clipboard':'desktop','desktop.reveal':'desktop','management.write':'management.write','state.change':'navigation'};
const call=<T=any>(method:string,params?:unknown)=>window.cowork.call<T>(method,params);
Object.assign(readMethods,{'desktop.read':'desktop','desktop.profiles':'desktop','tasks.inspect':'conversations.read','tasks.agent':'conversations.read'});
Object.assign(writeMethods,{'desktop.control':'desktop','desktop.resize':'desktop','desktop.reply':'desktop','desktop.surface':'desktop','desktop.navigate':'desktop','desktop.command':'desktop','tasks.stop':'runs.control','tasks.resume':'runs.control','commands.execute':'navigation'});
Object.assign(readMethods,{'projects.list':'conversations.read','history.prepare':'conversations.read','conversations.export':'conversations.read','tasks.list':'conversations.read','files.describe':'files.read'});
Object.assign(writeMethods,{'projects.save':'conversations.write','projects.archive':'conversations.write','projects.restore':'conversations.write','conversations.pin':'conversations.write','conversations.restore':'conversations.write','conversations.reference':'conversations.write','history.edit':'conversations.write','history.delete':'conversations.write','history.undo':'conversations.write','dialogs.attach':'files.read','dialogs.choose':'dialogs','dialogs.save':'dialogs','dialogs.workspace':'dialogs','editors.saveCopy':'document.edit'});
function text(value:unknown,name:string,max=10*1024*1024):string {if(typeof value!=='string'||value.length>max)throw new Error(`${name}无效。`);return value;}
function draft(value:any){text(value?.text,'输入');if(!Array.isArray(value.attachments)||value.attachments.some((item:Attachment)=>typeof item.path!=='string'||typeof item.name!=='string'))throw new Error('附件格式无效。');return {text:value.text,attachments:value.attachments as Attachment[]};}

export class HostSDK {
  controller!:WorkbenchController & {registry:PluginRegistry};
  readonly store:HostStore;
  constructor(store:HostStore){this.store=store;}
  snapshot(lease:Lease) {
    const c=this.controller,caps=lease.plugin.capabilities,id=lease.scope.conversationId??c.selectedId.current;
    if(lease.mode==='preview')return {conversationId:'preview',route:'chat',theme:c.theme,conversations:[{id:'preview',title:'示例任务',meta:{}}],conversation:{id:'preview',title:'示例任务',meta:{},messages:[{id:'sample',role:'user',content:'整理资料，写下结论。'}]},draft:{text:'独立预览内容',attachments:[]},editor:{text:'# 示例文稿\n\n继续完善。',file:{name:'预览文稿.md'}},state:{},preview:true};
    return {conversationId:id,route:c.route,theme:c.theme,
      ...(caps.includes('conversations.read')?{conversations:c.conversations,conversation:conversationAt(this.store,id),events:this.store.get(this.store.sessionKey('events',id),[])}:{}),
      ...(caps.includes('drafts.read')?{draft:c.drafts.get(id).value}:{}),
      ...(caps.includes('document.read')?{editor:this.editor(lease)}:{}),
      state:this.store.get(this.stateKey(lease),{})};
  }
  stateKey(lease:Lease){return `plugin-state:${lease.plugin.id}:${lease.plugin.stateVersion||1}:${lease.scope.extensionId}`;}
  editor(lease:Lease,params:any={}){
    const id=params.conversationId??lease.scope.conversationId??this.controller.selectedId.current;
    const path=params.path??lease.scope.filePath??this.store.get(this.store.sessionKey('activeFile',id),'');
    return this.store.get<any[]>(this.store.sessionKey('editors',id),[]).find(item=>item.file.path===path);
  }
  assert(lease:Lease,mutation=false){
    if(!lease.active)throw new Error('此界面实例已失效，请在当前界面继续。');
    if(mutation&&(lease.mode!=='active'||this.controller.registry.switching))throw new Error('界面预览或切换期间不能修改工作内容。');
  }
  async invoke(lease:Lease,method:string,params:any={}) {
    this.assert(lease);
    if(method==='snapshot')return this.snapshot(lease);
    const capability=readMethods[method]||writeMethods[method],mutation=method in writeMethods;
    if(!capability||!lease.plugin.capabilities.includes(capability))throw new Error('插件未声明此操作所需的能力：'+method);
    if(lease.mode==='preview'){
      const sample=this.snapshot(lease);
      if(method==='conversations.list')return sample.conversations;
      if(method==='conversations.get')return sample.conversation;
      if(method==='drafts.get')return sample.draft;
      if(method==='editors.get')return sample.editor;
      if(method==='state.get')return {};
      throw new Error('预览只提供示例内容，不能访问或修改实际数据。');
    }
    this.assert(lease,mutation);
    const operation=this.dispatch(lease,method,params);
    const value=await (mutation?this.controller.registry.track(operation):operation);
    this.assert(lease);return value;
  }
  async dispatch(lease:Lease,method:string,p:any) {
    const c=this.controller,id=text(p.conversationId??lease.scope.conversationId??c.selectedId.current,'会话标识',200);
    const params={...p,conversation_id:id};delete params.conversationId;
    switch(method){
      case 'conversations.list':return call('conversation.list',p);
      case 'conversations.get':return call('conversation.get',params);
      case 'conversations.search':return call('conversation.search',p);
      case 'conversations.create':return c.newConversation(p.workspace);
      case 'conversations.select':return c.switchConversation(id);
      case 'conversations.rename':case 'conversations.archive':case 'conversations.delete':case 'conversations.branch':case 'conversations.preferences':case 'conversations.pin':case 'conversations.restore':case 'conversations.reference':case 'conversations.export':{
        const result=await call(method.replace('conversations.','conversation.'),params);await c.refreshList();return result;
      }
      case 'projects.list':case 'projects.save':case 'projects.archive':case 'projects.restore':return call(method.replace('projects.','project.'),p);
      case 'history.prepare':case 'history.undo':return call(method,params);
      case 'history.edit':case 'history.delete':{
        const prepared=await call('history.prepare',params);this.assert(lease,true);
        const choice=await c.confirm(method==='history.edit'?'修改历史消息':'删除这段历史',`这会移除后续 ${prepared.downstream} 条消息。完成后可撤销。`,['取消','继续']);
        this.assert(lease,true);if(choice===0)return {cancelled:true};return call(method,params);
      }
      case 'drafts.get':return c.drafts.get(id).value;
      case 'drafts.change':c.drafts.change(id,draft(p.value));return c.drafts.get(id).value;
      case 'drafts.save':return c.drafts.save(id);
      case 'runs.send':return c.send(id);
      case 'runs.stop':{const conversation=await call<Conversation>('conversation.get',{conversation_id:id});return call('conversation.stop',{conversation_id:id,run_id:p.runId||conversation.run?.id});}
      case 'runs.respond':return call('interaction.respond',{...params,run_id:p.runId,request_id:p.requestId});
      case 'editors.get':return this.editor(lease,p);
      case 'editors.open':{
        const file=await call<FileSession>('file.open',{conversation_id:id,path:text(p.path,'文件路径')});this.assert(lease,true);
        const existing=this.store.get<any[]>(this.store.sessionKey('editors',id),[]).find(item=>item.file.path===file.path);
        const value=file.payload.text??file.payload.content??'';
        if(!existing)this.store.set<any[]>(this.store.sessionKey('editors',id),previous=>[...(previous||[]),{file,text:value,saved:value}]);
        this.store.set(this.store.sessionKey('activeFile',id),file.path);this.store.set(this.store.sessionKey('workspaceOpen',id),true);return existing?.file||file;
      }
      case 'editors.change':{
        const editor=this.editor(lease,p);if(!editor)throw new Error('编辑会话已关闭。');
        const value=text(p.text,'编辑内容');this.store.set<any[]>(this.store.sessionKey('editors',id),previous=>previous.map(item=>item.file.path===editor.file.path?{...item,text:value}:item));return {saved:false};
      }
      case 'editors.save':{
        const editor=this.editor(lease,p);if(!editor||editor.file.payload.read_only)throw new Error('此文件不可保存。');
        const value=editor.text,result=await call('file.save',{conversation_id:id,id:editor.file.id,payload:['text','html'].includes(editor.file.payload.kind)?value:JSON.parse(value)});
        this.store.set<any[]>(this.store.sessionKey('editors',id),previous=>previous.map(item=>item.file.id===editor.file.id?{...item,saved:value,file:{...item.file,fingerprint:result.fingerprint}}:item));return result;
      }
      case 'editors.saveCopy':{
        const editor=this.editor(lease,p);if(!editor)throw new Error('编辑会话已关闭。');
        return call('file.save_copy',{conversation_id:id,id:editor.file.id,name:text(p.name,'副本名称'),payload:['text','html'].includes(editor.file.payload.kind)?editor.text:JSON.parse(editor.text)});
      }
      case 'files.list':return call('workspace.list',params);
      case 'files.create':return call('workspace.create',params);
      case 'files.describe':return call('file.describe',params);
      case 'navigation.go':return c.goRoute(text(p.route,'页面标识',80));
      case 'theme.set':{
        await call('settings.theme',{theme:p.theme});
        const settings=await call<Settings>('settings.get');
        c.setSettings(settings);return settings;
      }
      case 'dialogs.confirm':if(!Array.isArray(p.choices)||p.choices.length<1||p.choices.length>4)throw new Error('对话框选项无效。');return c.confirm(text(p.title,'标题',200),text(p.detail,'说明',10000),p.choices.map((value:unknown)=>text(value,'选项',100)));
      case 'dialogs.attach':return call('dialog.attach',{conversation_id:id});
      case 'dialogs.choose':case 'dialogs.save':case 'dialogs.workspace':return call(method.replace('dialogs.','dialog.'),p);
      case 'desktop.tabs':return call('workbench.list',params);
      case 'desktop.profiles':return call('workbench.profiles',params);
      case 'desktop.read':case 'desktop.control':case 'desktop.resize':case 'desktop.reply':return call(method.replace('desktop.','workbench.'),{...params,tab_id:p.tabId||lease.scope.tabId});
      case 'desktop.surface':{
        const frame=lease.bounds?.(),rect=p.bounds;
        if(!frame||!rect||['x','y','width','height'].some(key=>!Number.isFinite(rect[key]))||rect.x<0||rect.y<0||rect.width<1||rect.height<1||rect.x+rect.width>frame.width+1||rect.y+rect.height>frame.height+1)throw new Error('网页显示区域无效。');
        return call('browser.surface',{conversation_id:id,tab_id:p.tabId||lease.scope.tabId,bounds:{x:frame.x+rect.x,y:frame.y+rect.y,width:rect.width,height:rect.height}});
      }
      case 'desktop.navigate':case 'desktop.command':return call(method.replace('desktop.','browser.'),{...params,tab_id:p.tabId||lease.scope.tabId});
      case 'desktop.open':if(!['web','terminal'].includes(p.kind))throw new Error('不支持的工作区类型。');return call(p.kind==='web'?'browser.open':'workbench.open',params);
      case 'desktop.close':return call(p.kind==='web'?'browser.close':'workbench.close',{...params,tab_id:p.tabId||lease.scope.tabId});
      case 'desktop.input':return call('workbench.input',{...params,tab_id:p.tabId||lease.scope.tabId});
      case 'desktop.clipboard':return call('clipboard.write',{text:text(p.text,'复制内容')});
      case 'desktop.reveal':return call('file.reveal',{...params,path:text(p.path,'文件路径')});
      case 'management.read':case 'management.write':{
        const methods:readonly string[]=method==='management.read'?managementReads:managementWrites;
        if(!methods.includes(p.action))throw new Error('不支持的管理操作。');return call(p.action,p.params||{});
      }
      case 'tasks.inspect':return call('task.inspect',{...params,run_id:p.runId});
      case 'tasks.list':return call('agent.list',params);
      case 'tasks.agent':case 'tasks.stop':case 'tasks.resume':return call(method==='tasks.agent'?'agent.get':method.replace('tasks.','agent.'),{...params,agent_id:p.agentId,run_id:p.runId});
      case 'commands.execute':{
        const command=c.registry.command(text(p.command,'命令',100));
        if(command.plugin.id!==lease.plugin.id)throw new Error('插件只能执行自己声明的命令。');
        return this.executeCommand(command.extension.command!);
      }
      case 'state.get':return this.store.get(this.stateKey(lease),{});
      case 'state.change':if(JSON.stringify(p.value).length>1024*1024)throw new Error('界面状态过大。');this.store.set(this.stateKey(lease),p.value);return {preserved:false};
      default:throw new Error('不支持的 SDK 操作。');
    }
  }
  executeCommand(command:string){const owner=this.controller.registry.command(command);this.store.set('plugin:command',{id:crypto.randomUUID(),pluginId:owner.plugin.id,extensionId:owner.extension.id,command});return {dispatched:true};}
}
