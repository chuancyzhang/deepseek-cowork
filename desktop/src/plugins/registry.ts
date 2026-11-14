import type {Extension,Plugin} from '../types';
import type {SaveCoordinator} from '../host/saveCoordinator';
export type Contribution={plugin:Plugin;extension:Extension};
export type Inventory={plugins:Plugin[];selected:Record<string,string[]>;catalog?:Plugin[]};
type Candidate={plugin:Plugin;ready:Set<string>;expected:Set<string>;failure:string};
const call=<T=any>(method:string,params?:unknown)=>window.cowork.call<T>(method,params);

/** Selection and readiness belong to the host, independently of the active layout. */
export class PluginRegistry {
  inventory:Inventory={plugins:[],selected:{}};
  candidate:Candidate|null=null;
  version=0;
  recoveryEpoch=0;
  listeners=new Set<()=>void>();
  pending=new Set<Promise<unknown>>();
  switching=false;
  changed() {this.version++; for(const listener of this.listeners) listener();}
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return ()=>{this.listeners.delete(listener);};};
  snapshot=()=>this.version;
  async refresh(){this.inventory=await call<Inventory>('plugins.inventory');this.changed();}
  resolve(kind:Extension['kind'],target:string):Contribution[] {
    const refs=this.inventory.selected[`${kind}:${target}`]||this.inventory.selected[`${kind}:*`]||[];
    return refs.flatMap(ref=>this.inventory.plugins.flatMap(plugin=>plugin.contributes.filter(extension=>`${plugin.id}/${extension.id}`===ref).map(extension=>({plugin,extension}))));
  }
  contributions(plugin:Plugin,kind:Extension['kind'],target:string) {
    return plugin.contributes.filter(extension=>extension.kind===kind && [target,'*'].includes(extension.target)).map(extension=>({plugin,extension}));
  }
  ready(key:string,hash:string){if(this.candidate?.plugin.hash===hash){this.candidate.ready.add(key);this.changed();}}
  failed(message:string,hash:string){if(this.candidate?.plugin.hash===hash){this.candidate.failure=message;this.changed();}}
  expect(key:string,hash:string){if(this.candidate?.plugin.hash===hash)this.candidate.expected.add(key);}
  track<T>(operation:Promise<T>):Promise<T>{this.pending.add(operation);void operation.finally(()=>this.pending.delete(operation)).catch(()=>{});return operation;}
  async apply(plugin:Plugin,saves:SaveCoordinator) {
    if(this.switching)throw new Error('另一个界面正在切换，请稍候。');
    if(!plugin.token)throw new Error('请先预览候选版本。');
    this.switching=true;
    try {
      await Promise.all([...this.pending]);await saves.preserve('plugin');
      const staged=await call<Plugin>('plugins.stage',{token:plugin.token});
      this.candidate={plugin:staged,ready:new Set(),expected:new Set(),failure:''};this.changed();
      // Slots and offscreen probes register before readiness is evaluated.
      await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
      await new Promise<void>((resolve,reject)=>{
        const check=()=>{
          const candidate=this.candidate;
          if(candidate?.failure){finish(new Error(candidate.failure));return;}
          if(candidate?.expected.size && [...candidate.expected].every(key=>candidate.ready.has(key)))finish();
        };
        const finish=(error?:Error)=>{clearTimeout(timer);unsubscribe();error?reject(error):resolve();};
        const timer=setTimeout(()=>finish(new Error('候选界面未能就绪，当前界面和编辑内容已保留。')),15000);
        const unsubscribe=this.subscribe(check);check();
      });
      await saves.preserve('plugin');
      await call('plugins.install',{token:plugin.token});
      await this.refresh();
    } finally {this.candidate=null;this.switching=false;this.changed();}
  }
  private async changeSelection(method:string,params:unknown,saves:SaveCoordinator){
    if(this.switching)throw new Error('另一个界面正在切换，请稍候。');
    this.switching=true;
    try{
      await Promise.all([...this.pending]);await saves.preserve('plugin');
      this.inventory=await call<Inventory>(method,params);
      if(method==='plugins.recover')this.recoveryEpoch++;
    }finally{this.switching=false;this.changed();}
  }
  async recover(saves:SaveCoordinator){await this.changeSelection('plugins.recover',{},saves);}
  async disable(id:string,saves:SaveCoordinator){await this.changeSelection('plugins.disable',{id},saves);}
  command(id:string){
    for(const plugin of this.inventory.plugins.filter(plugin=>plugin.enabled)){
      const extension=plugin.contributes.find(item=>item.kind==='command'&&item.command===id);
      if(extension)return {plugin,extension};
    }
    throw new Error('此命令不存在或所属插件已停用。');
  }
}
