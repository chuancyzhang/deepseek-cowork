import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowLeft, ArrowUp, Check, ChevronRight, Copy, ListChecks, LoaderCircle, Play, RefreshCw, Square, CircleAlert, Users, Wrench } from 'lucide-react';
import MessageBubble, { AssistantTurn } from './MessageBubble';
import {agentStatuses as statuses, childMessages} from './subagents';
import type { Conversation, Message } from './types';
import {reviewId} from './reviews';
import { messageGroups, formatDuration, toolErrorText } from './runtime';
import {ToolOutputDetail} from './ToolOutput';

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
export type TaskView = { section: 'calls' | 'agents' | 'context'; callId?: string; runId?: string; agentId?: string; scroll?: Record<string, number>; open?: Record<string, boolean>; drafts?: Record<string, {text: string; messageId?: string; submitted?: string}> };
type Props = { cid: string; conversation: Conversation; value: TaskView; change: (value: Partial<TaskView>) => void; error: (message: string) => void; openReview?: (id: string) => void; openFile?: (path: string) => void; videoTasks?: ReactNode };
const stringify = (value: any) => typeof value === 'string' ? value : JSON.stringify(value ?? null, null, 2);
const mediaSnapshot = (item: any) => ['media_create', 'media_task'].includes(item?.name) && Boolean(item?.result?.task_id);
const callStatus = (item: any) => mediaSnapshot(item) ? '已返回' : statuses[item?.status] || '';

export default function TaskWorkspace({ openReview, openFile = () => {}, cid, conversation, value, change, error, videoTasks }: Props) {
  const agentsOnly = value.section === 'agents';
  const runId = agentsOnly ? undefined : value.runId || conversation.run?.id;
  const historical = Boolean(value.runId && value.runId !== conversation.run?.id);
  const [snapshot, setSnapshot] = useState<any>(null), [child, setChild] = useState<any>(null), [failure, setFailure] = useState(''), [busy, setBusy] = useState(false), [copied, setCopied] = useState('');
  const scroll = useRef<HTMLDivElement>(null), latest = useRef({value, change, cid}); latest.current = {value, change, cid};
  const mounted = useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const loadedScope = useRef('');
  const actionSequence = useRef(0);
  const [actionError, setActionError] = useState('');
  const route = [value.section, value.callId || '', value.agentId || '', runId || ''].join(':');
  const [revision, setRevision] = useState(0);
  const [request, setRequest] = useState<any>(null), [requestError, setRequestError] = useState(''), [requestBusy, setRequestBusy] = useState(false);
  const requestSequence = useRef(0);
  useEffect(() => {requestSequence.current++; setRequest(null); setRequestError(''); setRequestBusy(false);}, [cid, runId]);
  const contextScope = useRef(''); contextScope.current = cid + ':' + (runId || '');
  async function readRequest() {
    const scope = contextScope.current;
    const sequence = ++requestSequence.current;
    const current = () => contextScope.current === scope && requestSequence.current === sequence;
    setRequestBusy(true); setRequestError('');
    try {const result = await call('task.context', {conversation_id: cid, run_id: runId}); if (current()) setRequest(result);}
    catch (failure) {if (current()) setRequestError((failure as Error).message);}
    finally {if (current()) setRequestBusy(false);}
  }
  useEffect(() => {
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const result = agentsOnly ? {agents:await call('agent.list', {conversation_id:cid})} : await call('task.inspect', {conversation_id: cid, run_id: runId});
        const agent = value.agentId ? await call('agent.get', {conversation_id: cid, agent_id: value.agentId}) : null;
        if (stopped) return;
        setSnapshot(result); setChild((previous: any) => {
          if (!agent || !previous || previous.summary?.id !== agent.summary?.id) return agent;
          const earliest = agent.messages?.[0]?.id;
          const boundary = previous.messages?.findIndex((message: any) => message.id === earliest) ?? -1;
          return boundary > 0 ? {...agent, activity:[...(previous.activity || []).filter((item:any)=>!(agent.activity || []).some((next:any)=>next.run_id===item.run_id && next.sequence===item.sequence)), ...(agent.activity || [])], before: previous.before, has_more: previous.has_more, messages: [...previous.messages.slice(0, boundary), ...agent.messages]} : agent;
        }); setFailure('');
        timer = setTimeout(update, ['running', 'queued', 'stopping'].includes(result.run?.status) || result.agents?.some((item:any)=>['running','queued','stopping'].includes(item.status)) || agent && ['running', 'queued', 'stopping'].includes(agent.summary?.status) ? 1000 : 4000);
      } catch (failure) { if (!stopped) setFailure((failure as Error).message); }
    }
    const scope = cid+':'+(runId || '')+':'+(value.agentId || '')+':'+agentsOnly;
    if (loadedScope.current !== scope) {loadedScope.current=scope;setSnapshot(null);setChild(null);setActionError('');setBusy(false);actionSequence.current++;}
    void update();
    return () => { stopped = true; clearTimeout(timer); };
  }, [cid, runId, value.agentId, agentsOnly, revision, historical ? undefined : conversation.run?.status]);
  useLayoutEffect(() => {
    if (scroll.current && snapshot) scroll.current.scrollTop = latest.current.value.scroll?.[route] || 0;
  }, [route, Boolean(snapshot), Boolean(child)]);
  async function copy(key: string, text: string) { try { await call('clipboard.write', {text}); setCopied(key); } catch (failure) { error((failure as Error).message); } }
  function copyButton(key: string, text: string) { return <button className="icon-button" title="复制完整内容" onClick={event => {event.preventDefault(); event.stopPropagation(); void copy(key, text);}}>{copied === key ? <Check size={14}/> : <Copy size={14}/>}</button>; }
  function disclosure(id: string, title: string, content: any, defaultOpen = false) {
    const text = stringify(content);
    return <details open={value.open?.[id] ?? defaultOpen} onToggle={event => { if ((value.open?.[id] ?? defaultOpen) !== event.currentTarget.open) change({open: {...value.open, [id]: event.currentTarget.open}}); }}><summary>{title}{copyButton(id, text)}</summary><pre>{text}</pre></details>;
  }
  const projected = useMemo(()=>childMessages(child),[child]);
  const groups = useMemo(()=>messageGroups(projected),[projected]);
  const childCalls = useMemo(()=>{
    const calls=new Map<string,any>();
    for (const message of projected) {
      for (const tool of message.tool_calls || []) calls.set(tool.id,{...calls.get(tool.id),id:tool.id,name:tool.function?.name || tool.name,arguments:tool.function?.arguments ?? tool.arguments,
        status:message.meta?.turn_id===child?.summary?.run_id && ['running','queued','stopping'].includes(child?.summary?.status) ? 'running' : 'unknown'});
      if (message.role==='tool' && message.tool_call_id) calls.set(message.tool_call_id,{id:message.tool_call_id,name:message.meta?.tool_name || '工具调用',...calls.get(message.tool_call_id),
        result:message.result_obj ?? message.content,error:message.result_obj?.error,status:message.result_obj?.status || (message.result_obj?.error ? 'failed' : 'completed')});
    }
    for (const event of child?.activity || []) {
      const data=event.payload || event,item=calls.get(data.tool_call_id);
      if (item && data.event_type==='tool_result' && typeof data.duration==='number') item.elapsed_ms=data.duration*1000;
    }
    return [...calls.values()];
  },[projected,child]);
  const selected = (agentsOnly ? childCalls : snapshot?.calls)?.find((item: any) => item.id === value.callId);
  const selectedAgent = child?.summary;
  const agentId = value.agentId || '';
  const draft = value.drafts?.[agentId] || {text: ''};
  const childRunning = ['running', 'queued', 'stopping'].includes(selectedAgent?.status);
  const childReadOnly = selectedAgent?.read_only || ['closed', 'killed'].includes(selectedAgent?.status);
  function updateDraft(next: typeof draft) { change({drafts: {...value.drafts, [agentId]: next}}); }
  async function agentAction(method: 'agent.send' | 'agent.stop' | 'agent.resume') {
    if (busy) return;
    setBusy(true); setActionError('');
    const sequence=++actionSequence.current;
    const currentScope = () => mounted.current && latest.current.cid === cid && latest.current.value.agentId === agentId;
    try {
      const messageId = draft.messageId || crypto.randomUUID();
      if (method === 'agent.send') updateDraft({...draft, messageId, submitted: draft.submitted ?? draft.text});
      await call(method, {conversation_id: cid, agent_id: agentId, run_id: selectedAgent?.run_id || selectedAgent?.meta?.run_id, content: draft.submitted ?? draft.text, message_id: messageId});
      if (!mounted.current || latest.current.cid !== cid) return;
      if (method === 'agent.send') {
        const current = latest.current.value.drafts?.[agentId];
        latest.current.change({drafts: {...latest.current.value.drafts, [agentId]: {text: current?.text === (draft.submitted ?? draft.text) ? '' : current?.text || ''}}});
      }
      setRevision(previous => previous + 1);
    } catch (failure) {if (currentScope()) setActionError((failure as Error).message);} finally {if (mounted.current && actionSequence.current===sequence) setBusy(false);}
  }
  return <div className={'task-workspace' + (agentsOnly ? ' agent-workspace' : '')}>
    {value.callId || value.agentId ? <div className="task-detail-heading"><button className="icon-button" title={value.callId ? agentsOnly ? '返回子任务对话' : '返回调用记录' : '返回子任务'} onClick={() => change({callId: undefined, ...(!value.callId ? {agentId:undefined} : {})})}><ArrowLeft size={17}/></button><strong>{selected?.name || selectedAgent?.name || (value.callId ? '调用详情' : '子任务')}</strong><span>{selected ? callStatus(selected) || selected.status : statuses[selectedAgent?.status] || selectedAgent?.status || ''}</span>{selected?.elapsed_ms != null && <small>{(selected.elapsed_ms / 1000).toFixed(1)} 秒</small>}</div> : agentsOnly ? null : <div className="task-sections" role="tablist" aria-label="任务视图">{([['calls', '调用记录'], ['context', '执行上下文']] as const).map(([id, name]) => <button key={id} role="tab" aria-selected={value.section === id} className={value.section === id ? 'active' : ''} onClick={() => change({section: id})}>{name}</button>)}</div>}
    {historical && <div className="task-scope"><span>正在查看历史任务</span><button className="text-link" onClick={() => change({runId: undefined, callId: undefined, agentId: undefined})}>返回当前任务</button></div>}
    {failure && <div className="inline-error" role="alert">{failure}<button onClick={() => setRevision(previous => previous + 1)}><RefreshCw size={13}/>重试</button></div>}
    <div className="task-scroll" ref={scroll} onScroll={event => { const top = event.currentTarget.scrollTop; change({scroll: {...value.scroll, [route]: top}}); }}>
      {value.section === 'calls' && !value.callId && videoTasks}
      {!snapshot && !failure ? <div className="loading"><LoaderCircle size={18} className="spin"/>正在读取任务…</div> : value.callId ? selected ? <div className="call-detail">{reviewId(selected.result) && openReview && <button className="primary" onClick={()=>openReview!(reviewId(selected.result)!)}>打开草稿审阅</button>}{selected.error && <p className="run-error">{toolErrorText(selected.error)}</p>}{disclosure('arguments:' + selected.id, '参数', selected.arguments)}{!agentsOnly && <ToolOutputDetail key={cid+':'+runId+':'+selected.id} cid={cid} runId={runId} callId={selected.id} running={['running','queued','stopping'].includes(snapshot?.run?.status)}/>}{mediaSnapshot(selected) && <p className="muted">以下为调用返回时的记录。最新进度请看聊天中的任务卡。</p>}{disclosure('result:' + selected.id, mediaSnapshot(selected) ? '调用返回时的结果' : '返回值', selected.result ?? '尚未收到结果', true)}{disclosure('info:' + selected.id, '运行信息', {id: selected.id, run_id: snapshot.run?.id, started_at: selected.started_at, elapsed_ms: selected.elapsed_ms, waits: selected.waits})}</div> : <p className="task-empty">尚未找到这次调用的记录。<button onClick={() => setRevision(previous => previous + 1)}>重新读取</button></p> : value.agentId ? <>
        {selectedAgent?.control_error && <div className="inline-error">{selectedAgent.control_error}</div>}
        {selectedAgent?.last_error && <p className="run-error">{toolErrorText(selectedAgent.last_error)}</p>}
        {child?.has_more && <button className="load-history" disabled={busy} onClick={async () => {
          const scope=loadedScope.current; setBusy(true);
          try {const older=await call('agent.get',{conversation_id:cid,agent_id:agentId,before:child.before});
            if (mounted.current && loadedScope.current===scope) setChild((prior:any)=>({...prior,before:older.before,has_more:older.has_more,activity:[...(older.activity || []),...(prior.activity || [])],messages:[...older.messages.filter((message:any)=>!prior.messages.some((item:any)=>item.id===message.id)),...prior.messages]}));
          } catch (failure) {if (mounted.current && loadedScope.current===scope) setActionError((failure as Error).message);} finally {if(mounted.current)setBusy(false);}
        }}>更早的消息</button>}
        <div className="child-conversation">{groups.map(group=>{
          const message=group.messages[0], lastRun=message.meta?.turn_id===selectedAgent?.run_id;
          const action=(action:string,item:Message)=>{if(action==='tool')change({callId:item.tool_call_id});};
          return group.assistant ? <AssistantTurn key={group.id} messages={group.messages} cid={cid} active={false} readOnly onAction={action} onFile={openFile}
            activeRun={lastRun && childRunning ? selectedAgent.run_id : undefined} stopped={lastRun && selectedAgent?.status==='interrupted'} statusLabel={selectedAgent?.status==='stopping' ? '正在停止' : '正在处理'}
            timing={lastRun ? {id:selectedAgent.run_id,start:selectedAgent.started_at || undefined,end:childRunning ? undefined : selectedAgent.finished_at || undefined,thinking:{}} : undefined}/>
            : <MessageBubble key={group.id} message={message} cid={cid} active={false} readOnly onAction={action} onFile={openFile}/>;
        })}</div>
        {child?.inbox?.filter((item: any) => ['pending','queued','claimed'].includes(item.status || item.state)).map((item: any) => <p className="muted child-pending" key={item.message_id}>待处理：{item.content}</p>)}
      </> : value.section === 'calls' ? <>
        {snapshot?.calls?.length ? snapshot.calls.map((item: any) => <button className="call-row" key={item.id} onClick={() => change({callId: item.id})}><Wrench size={16}/><span><strong>{item.name}</strong><small>{callStatus(item) || item.status}{item.elapsed_ms != null ? ` · ${(item.elapsed_ms / 1000).toFixed(1)} 秒` : ''}</small>{item.error && <small className="error-text">{toolErrorText(item.error)}</small>}</span><ChevronRight size={14}/></button>) : <p className="task-empty"><ListChecks size={22}/>本轮还没有工具调用</p>}
      </> : value.section === 'agents' ? snapshot?.agents?.length ? <div className="agent-list">{[
        snapshot.agents.filter((agent:any)=>['running','queued','stopping','waiting'].includes(agent.status)),
        snapshot.agents.filter((agent:any)=>!['running','queued','stopping','waiting','completed','closed','killed'].includes(agent.status)),
        snapshot.agents.filter((agent:any)=>['completed','closed','killed'].includes(agent.status))
      ].filter(group=>group.length).map((group,index)=><div className="agent-list-group" key={index}>{group.map((agent:any)=>{
        const running=['running','queued','stopping'].includes(agent.status), failed=Boolean(agent.last_error || agent.control_error || agent.status==='failed');
        const Icon=running ? LoaderCircle : failed ? CircleAlert : ['completed','closed'].includes(agent.status) ? Check : Users;
        return <button className="agent-row" key={agent.id} onClick={()=>change({agentId:agent.id})}>
          <Icon size={16} className={running ? 'spin' : undefined}/><span><strong>{agent.name || '子任务'}</strong><small className={failed ? 'error-text' : undefined}>{agent.control_error || toolErrorText(agent.last_error) || agent.latest_progress || agent.last_result || agent.meta?.task || agent.description}</small></span>
          <span className="agent-row-state"><span>{statuses[agent.status] || agent.status}</span>{typeof (agent.duration ?? agent.meta?.last_duration)==='number' && <small>{formatDuration(agent.duration ?? agent.meta.last_duration)}</small>}</span><ChevronRight size={14}/>
        </button>;
      })}</div>)}</div> : <p className="task-empty"><Users size={22}/>还没有子任务。在对话中让 AI 分工处理任务后，可在这里查看进展。</p> : <div className="execution-context">
        <div className="context-heading"><strong>当前选择</strong>{copyButton('current-context', stringify(snapshot?.current_context))}</div>
        <dl>{[['项目', conversation.meta.project_name || '独立聊天'], ['工作目录', snapshot?.current_context?.workspace], ['模型', snapshot?.current_context?.model], ['专家', snapshot?.current_context?.conversation_agent?.name || 'DeepSeek Cowork'], ['任务方式', snapshot?.current_context?.mode || '普通任务'], ['能力', snapshot?.current_context?.skills?.join('、') || '自动选择']].map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text || '未设置'}</dd></div>)}</dl>
        {disclosure('selected-references', '引用与资料', {conversations: snapshot?.current_context?.conversation_refs || [], knowledge: snapshot?.current_context?.knowledge_refs || []})}
        <div className="context-heading"><strong>{historical ? '该轮任务提交' : '本轮任务提交'}</strong>{snapshot?.submitted_context && copyButton('submitted-context', stringify(snapshot.submitted_context))}</div>
        {snapshot?.submitted_context ? <>{disclosure('submitted-context', '查看完整提交上下文', snapshot.submitted_context)}<p className="muted">来自{historical ? '该轮' : '本轮'}已接收的提交记录，当前选择将在下次任务生效。</p></> : <p className="muted">还没有提交任务。</p>}
        <div className="context-heading"><strong>{historical ? '该轮最近模型请求' : '最近模型请求'}</strong>{request && copyButton('request-context', stringify(request))}<button className="text-link" disabled={requestBusy} onClick={() => void readRequest()}>{requestBusy ? '读取中…' : request ? '刷新' : '查看完整请求'}</button></div>
        {requestError && <p className="error-text" role="alert">{requestError}</p>}{request && <>{disclosure('request-context', '请求原文', request, true)}<p className="muted">{request.scope}</p></>}
      </div>}
    </div>
    {actionError && <div className="inline-error" role="alert">{actionError}</div>}
    {value.agentId && !value.callId && selectedAgent && !childReadOnly && <div className="child-composer"><textarea aria-label="补充子任务" placeholder="补充要求…" value={draft.text} onChange={event => updateDraft({...draft, text: event.target.value})} onKeyDown={event=>{if(event.key==='Enter' && !event.shiftKey && !event.nativeEvent.isComposing){event.preventDefault();if(!busy && (draft.text.trim() || draft.messageId))void agentAction('agent.send');}}}/><div>
      <span>{child?.inbox?.some((item:any)=>['pending','queued','claimed'].includes(item.status || item.state)) ? '补充已保存，等待处理' : ''}</span>
      {selectedAgent.status==='interrupted' && <button className="secondary" disabled={busy} onClick={()=>void agentAction('agent.resume')}><Play size={13}/>继续</button>}
      {childRunning && (draft.text.trim() || draft.messageId) && <button className="icon-button" title="停止子任务" disabled={busy || selectedAgent.status==='stopping'} onClick={()=>void agentAction('agent.stop')}><Square size={14}/></button>}
      {childRunning && !draft.text.trim() && !draft.messageId ? <button className="send-button" title={selectedAgent.status==='stopping' ? '正在停止' : '停止子任务'} disabled={busy || selectedAgent.status==='stopping'} onClick={()=>void agentAction('agent.stop')}><Square size={14}/></button>
        : <button className="send-button" title={draft.messageId ? '重试原补充' : '发送给子任务'} disabled={busy || !draft.text.trim() && !draft.messageId} onClick={()=>void agentAction('agent.send')}><ArrowUp size={17}/></button>}
    </div></div>}

  </div>;
}
