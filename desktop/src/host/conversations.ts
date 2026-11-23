import type {Conversation,HostEvent,Run,RuntimeEvent,MediaTask} from '../types';
import type {HostStore,Update} from './store';
import {mergeEvents,isActive} from '../runtime.ts';
import {mergeReviews,type ReviewSummary} from '../reviews.ts';
import type {WorkspaceView} from '../Workspace';

export function presentOpenedBrowser(store:HostStore,event:HostEvent,initial:()=>WorkspaceView) {
  const cid=event.conversation_id;
  if(event.event!=='browser_opened' || !cid || !event.run_id || !event.data?.tab_id)return false;
  const run=store.get<Record<string,Run>>('runs',{})[cid];
  if(run && run.id!==event.run_id)return false;
  const key=store.sessionKey('workspaceView',cid),view=store.get<WorkspaceView>(key,initial);
  if(view.browserPresentedRun===event.run_id)return false;
  const show=cid===store.get('navigation:selected','') && store.get('navigation:route','chat')==='chat'
    && !view.browserPresentationSuppressed && view.selected==='new';
  store.set(key,{...view,browserPresentedRun:event.run_id,...(show?{selected:'workbench:'+event.data.tab_id}:{})});
  if(show)store.set(store.sessionKey('workspaceOpen',cid),true);
  return show;
}

export function mergeMediaTasks(previous:MediaTask[], incoming:MediaTask[]) {
  const merged=new Map(previous.map(item=>[item.id,item]));let changed=false;
  for(const item of incoming)if(!merged.has(item.id)||item.updated_at>(merged.get(item.id)?.updated_at||0)){merged.set(item.id,item);changed=true;}
  return changed?[...merged.values()].sort((a,b)=>a.created_at-b.created_at).slice(-100):previous;
}

/** Backend snapshots and durable increments enter the same host projection. */
export function receiveRun(store:HostStore,run:Run|null,id:string) {
  const previous=store.get<Record<string,Run>>('runs',{})[id];
  if(run && previous && (run.id===previous.id ? (run.updated_at||0)<(previous.updated_at||0) : (run.created_at||0)<(previous.created_at||0)))return;
  if(!run&&previous)return; // A snapshot without a run cannot erase a later accepted run.
  if(run)store.set<Record<string,Run>>('runs',value=>({...value,[id]:run}));
}
export function conversationAt(store:HostStore,id:string):Conversation|null {
  const value=store.get<Conversation|null>(store.sessionKey('current',id),null);
  return value?{...value,run:store.get<Record<string,Run>>('runs',{})[id]||null,reviews:store.get<ReviewSummary[]>(store.sessionKey('reviews',id),[]),media_tasks:store.get<MediaTask[]>(store.sessionKey('media_tasks',id),[])}:null;
}
export function receiveConversation(store:HostStore,id:string,update:Update<Conversation|null>) {
  const previous=conversationAt(store,id),value=typeof update==='function'?update(previous):update;
  if(!value){store.set(store.sessionKey('current',id),null);return;}
  if(value.run===null&&!isActive(previous?.run?.status))store.set<Record<string,Run>>('runs',runs=>Object.fromEntries(Object.entries(runs).filter(([key])=>key!==id)));
  if('run' in value)receiveRun(store,value.run||null,id);
  const {run,reviews,media_tasks,...body}=value;
  if(media_tasks)store.set<MediaTask[]>(store.sessionKey('media_tasks',id),previous=>mergeMediaTasks(previous||[],media_tasks));
  if(reviews)store.set<ReviewSummary[]>(store.sessionKey('reviews',id),previous=>mergeReviews(previous||[],reviews));
  store.set(store.sessionKey('current',id),body);
}
export function receiveEvents(store:HostStore,batch:HostEvent[]) {
  const grouped=new Map<string,RuntimeEvent[]>();
  for(const event of batch){
    const id=event.conversation_id;if(!id)continue;
    if(event.run)receiveRun(store,event.run,id);
    if(event.event==='media_task' && event.data?.id && event.data.conversation_id===id)store.set<MediaTask[]>(store.sessionKey('media_tasks',id),previous=>mergeMediaTasks(previous||[],[event.data]));
    if(event.event==='review_changed' && event.data?.id)store.set<ReviewSummary[]>(store.sessionKey('reviews',id),previous=>mergeReviews(previous||[],[event.data]));
    if(event.event!=='runtime')continue;
    if(!grouped.has(id))grouped.set(id,[]);grouped.get(id)!.push(event.data);
    const key=store.sessionKey('interaction',id);
    if(event.data.type==='interaction_request')store.set(key,event.data.data);
    if(event.data.type==='interaction_finished')store.set<any>(key,(previous:any)=>previous?.request_id===event.data.data?.request_id?null:previous);
  }
  for(const [id,events] of grouped)store.set<RuntimeEvent[]>(store.sessionKey('events',id),previous=>mergeEvents(previous||[],events));
}
