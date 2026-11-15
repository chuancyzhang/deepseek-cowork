import BuiltinWorkbench from './BuiltinWorkbench';
import PluginSlot from './PluginSlot';
import CreationWorkbench from './creation/CreationWorkbench';
import {Component,type ErrorInfo,type ReactNode,useSyncExternalStore} from 'react';
import {useWorkbench} from '../host/Host';
import {useConversation} from '../host/context';
class ViewBoundary extends Component<{children:ReactNode;recover:()=>void;resetKey:string;conversationId?:string;runId?:string},{error:string}>{
  state={error:''};
  static getDerivedStateFromError(error:Error){return {error:error.message};}
  componentDidUpdate(previous:Readonly<{resetKey:string}>){if(this.state.error&&previous.resetKey!==this.props.resetKey)this.setState({error:''});}
  componentDidCatch(error:Error,info:ErrorInfo){console.error('workbench_render_failed',JSON.stringify({conversation_id:this.props.conversationId,run_id:this.props.runId,message:error.message,component_stack:info.componentStack}));}
  render(){return this.state.error?<div className="loading workbench-failure" role="alert"><p>工作台界面未能显示，输入和后台任务仍由宿主保留。</p><details><summary>具体原因</summary>{this.state.error}</details><button className="secondary" onClick={this.props.recover}>界面恢复</button></div>:this.props.children;}
}
export default function WorkbenchMount(){
  const {host,registry,creationOpen,loading}=useWorkbench();useSyncExternalStore(registry.subscribe,registry.snapshot);
  const [conversation]=useConversation();
  const key=registry.resolve('workbench','main').map(item=>item.plugin.hash).join(':');
  const creation = creationOpen && conversation?.meta.creation_session && registry.inventory.modes?.some(item=>item.mode==='creation'&&item.enabled);
  return <ViewBoundary conversationId={conversation?.id} runId={conversation?.run?.id} resetKey={key+':'+registry.recoveryEpoch+':'+creation} recover={()=>host.set('host:recoveryOpen',true)}>{creation?loading?<div className="loading" role="status">正在恢复设计工作台…</div>:<CreationWorkbench key={conversation?.id}><BuiltinWorkbench/></CreationWorkbench>:<PluginSlot kind="workbench" target="main"><BuiltinWorkbench/></PluginSlot>}</ViewBoundary>;
}
