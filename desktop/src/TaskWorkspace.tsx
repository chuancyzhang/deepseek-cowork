import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowUp, Check, ChevronRight, Copy, ListChecks, LoaderCircle, Play, RefreshCw, Square, Users, Wrench } from 'lucide-react';
import { Markdown } from './MessageBubble';
import type { Conversation } from './types';
import { messageText } from './runtime';

const call = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
export type TaskView = { section: 'calls' | 'agents' | 'context'; callId?: string; runId?: string; agentId?: string; scroll?: Record<string, number>; open?: Record<string, boolean>; drafts?: Record<string, {text: string; messageId?: string; submitted?: string}> };
type Props = { cid: string; conversation: Conversation; value: TaskView; change: (value: Partial<TaskView>) => void; error: (message: string) => void };
const statuses: Record<string, string> = { queued: '排队中', running: '运行中', stopping: '正在停止', completed: '已完成', failed: '失败', interrupted: '已停止', idle: '待命', waiting: '等待中', closed: '已关闭', killed: '已结束', unknown: '状态未记录' };
const stringify = (value: any) => typeof value === 'string' ? value : JSON.stringify(value ?? null, null, 2);

export default function TaskWorkspace({ cid, conversation, value, change, error }: Props) {
  const runId = value.runId || conversation.run?.id;
  const historical = Boolean(value.runId && value.runId !== conversation.run?.id);
  const [snapshot, setSnapshot] = useState<any>(null), [child, setChild] = useState<any>(null), [failure, setFailure] = useState(''), [busy, setBusy] = useState(false), [copied, setCopied] = useState('');
  const scroll = useRef<HTMLDivElement>(null), latest = useRef({value, change}); latest.current = {value, change};
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
    catch (failure) {if (current()) setRequestError((failure as Error).message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, ''));}
    finally {if (current()) setRequestBusy(false);}
  }
  useEffect(() => {
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const result = await call('task.inspect', {conversation_id: cid, run_id: runId});
        const agent = value.agentId ? await call('agent.get', {conversation_id: cid, agent_id: value.agentId}) : null;
        if (stopped) return;
        setSnapshot(result); setChild((previous: any) => {
          if (!agent || !previous || previous.summary?.id !== agent.summary?.id) return agent;
          const earliest = agent.messages?.[0]?.id;
          const boundary = previous.messages?.findIndex((message: any) => message.id === earliest) ?? -1;
          return boundary > 0 ? {...agent, before: previous.before, has_more: previous.has_more, messages: [...previous.messages.slice(0, boundary), ...agent.messages]} : agent;
        }); setFailure('');
        timer = setTimeout(update, ['running', 'queued', 'stopping'].includes(result.run?.status) || agent && ['running', 'queued', 'stopping'].includes(agent.summary?.status) ? 1000 : 4000);
      } catch (failure) { if (!stopped) setFailure((failure as Error).message); }
    }
    setSnapshot(null); setChild(null); void update();
    return () => { stopped = true; clearTimeout(timer); };
  }, [cid, runId, value.agentId, revision]);
  useLayoutEffect(() => {
    if (scroll.current && snapshot) scroll.current.scrollTop = latest.current.value.scroll?.[route] || 0;
  }, [route, Boolean(snapshot), Boolean(child)]);
  async function copy(key: string, text: string) { try { await call('clipboard.write', {text}); setCopied(key); } catch (failure) { error((failure as Error).message); } }
  function copyButton(key: string, text: string) { return <button className="icon-button" title="复制完整内容" onClick={event => {event.preventDefault(); event.stopPropagation(); void copy(key, text);}}>{copied === key ? <Check size={14}/> : <Copy size={14}/>}</button>; }
  function disclosure(id: string, title: string, content: any, defaultOpen = false) {
    const text = stringify(content);
    return <details open={value.open?.[id] ?? defaultOpen} onToggle={event => { if ((value.open?.[id] ?? defaultOpen) !== event.currentTarget.open) change({open: {...value.open, [id]: event.currentTarget.open}}); }}><summary>{title}{copyButton(id, text)}</summary><pre>{text}</pre></details>;
  }
  const selected = snapshot?.calls?.find((item: any) => item.id === value.callId);
  const selectedAgent = child?.summary;
  const agentId = value.agentId || '';
  const draft = value.drafts?.[agentId] || {text: ''};
  const childRunning = ['running', 'queued', 'stopping'].includes(selectedAgent?.status);
  const childReadOnly = selectedAgent?.read_only || ['closed', 'killed'].includes(selectedAgent?.status);
  function updateDraft(next: typeof draft) { change({drafts: {...value.drafts, [agentId]: next}}); }
  async function agentAction(method: 'agent.send' | 'agent.stop' | 'agent.resume') {
    setBusy(true);
    try {
      const messageId = draft.messageId || crypto.randomUUID();
      if (method === 'agent.send') updateDraft({...draft, messageId, submitted: draft.submitted ?? draft.text});
      await call(method, {conversation_id: cid, agent_id: agentId, run_id: selectedAgent?.run_id || selectedAgent?.meta?.run_id, content: draft.submitted ?? draft.text, message_id: messageId});
      if (method === 'agent.send') {
        const current = latest.current.value.drafts?.[agentId];
        latest.current.change({drafts: {...latest.current.value.drafts, [agentId]: {text: current?.text === (draft.submitted ?? draft.text) ? '' : current?.text || ''}}});
      }
      setRevision(previous => previous + 1);
    } catch (failure) { error((failure as Error).message); } finally { setBusy(false); }
  }
  return <div className="task-workspace">
    {value.callId || value.agentId ? <div className="task-detail-heading"><button className="icon-button" title={value.callId ? '返回调用记录' : '返回子任务'} onClick={() => change({callId: undefined, agentId: undefined})}><ArrowLeft size={17}/></button><strong>{selected?.name || selectedAgent?.name || (value.callId ? '调用详情' : '子任务')}</strong><span>{statuses[selected?.status || selectedAgent?.status] || selectedAgent?.status || ''}</span>{selected?.elapsed_ms != null && <small>{(selected.elapsed_ms / 1000).toFixed(1)} 秒</small>}</div> : <div className="task-sections" role="tablist" aria-label="任务视图">{([['calls', '调用记录'], ['agents', '子任务'], ['context', '执行上下文']] as const).map(([id, name]) => <button key={id} role="tab" aria-selected={value.section === id} className={value.section === id ? 'active' : ''} onClick={() => change({section: id})}>{name}</button>)}</div>}
    {historical && <div className="task-scope"><span>正在查看历史任务</span><button className="text-link" onClick={() => change({runId: undefined, callId: undefined, agentId: undefined})}>返回当前任务</button></div>}
    {failure && <div className="inline-error" role="alert">{failure}<button onClick={() => setRevision(previous => previous + 1)}><RefreshCw size={13}/>重试</button></div>}
    <div className="task-scroll" ref={scroll} onScroll={event => { const top = event.currentTarget.scrollTop; change({scroll: {...value.scroll, [route]: top}}); }}>
      {!snapshot && !failure ? <div className="loading"><LoaderCircle size={18} className="spin"/>正在读取任务…</div> : value.callId ? selected ? <div className="call-detail">{selected.error && <p className="run-error">{selected.error}</p>}{disclosure('arguments:' + selected.id, '参数', selected.arguments, true)}{disclosure('result:' + selected.id, '结果', selected.result ?? '尚未收到结果', true)}{disclosure('info:' + selected.id, '运行信息', {id: selected.id, run_id: snapshot.run?.id, started_at: selected.started_at, elapsed_ms: selected.elapsed_ms})}</div> : <p className="task-empty">尚未找到这次调用的记录。<button onClick={() => setRevision(previous => previous + 1)}>重新读取</button></p> : value.agentId ? <>
        {selectedAgent?.control_error && <div className="inline-error">{selectedAgent.control_error}</div>}
        {child?.has_more && <button className="load-history" disabled={busy} onClick={async () => { setBusy(true); try { const older = await call('agent.get', {conversation_id: cid, agent_id: agentId, before: child.before}); setChild((prior: any) => ({...prior, before: older.before, has_more: older.has_more, messages: [...older.messages, ...prior.messages]})); } catch (failure) { error((failure as Error).message); } finally { setBusy(false); } }}>更早的消息</button>}
        {child?.messages?.map((message: any) => <article className={'child-message ' + message.role} key={message.id}><strong>{message.role === 'user' ? '你' : selectedAgent?.name || '子任务'}</strong>{message.reasoning_content && disclosure('thinking:' + message.id, '深度思考', message.reasoning_content)}<div className="markdown"><Markdown text={messageText(message.content)}/></div></article>)}
        {child?.inbox?.filter((item: any) => ['pending','queued','claimed'].includes(item.status || item.state)).map((item: any) => <p className="muted" key={item.message_id}>待处理：{item.content}</p>)}
      </> : value.section === 'calls' ? <>
        {snapshot?.calls?.length ? snapshot.calls.map((item: any) => <button className="call-row" key={item.id} onClick={() => change({callId: item.id})}><Wrench size={16}/><span><strong>{item.name}</strong><small>{statuses[item.status] || item.status}{item.elapsed_ms != null ? ` · ${(item.elapsed_ms / 1000).toFixed(1)} 秒` : ''}</small>{item.error && <small className="error-text">{item.error}</small>}</span><ChevronRight size={14}/></button>) : <p className="task-empty"><ListChecks size={22}/>本轮还没有工具调用</p>}
      </> : value.section === 'agents' ? snapshot?.agents?.length ? snapshot.agents.map((agent: any) => <button className="call-row" key={agent.id || agent.agent_id} onClick={() => change({agentId: agent.id || agent.agent_id})}><Users size={17}/><span><strong>{agent.name || agent.task_name || '子任务'}</strong><small>{statuses[agent.status] || agent.status}</small><small>{agent.description || agent.task || agent.meta?.task}</small></span><ChevronRight size={14}/></button>) : <p className="task-empty"><Users size={22}/>还没有子任务</p> : <div className="execution-context">
        <div className="context-heading"><strong>当前选择</strong>{copyButton('current-context', stringify(snapshot?.current_context))}</div>
        <dl>{[['项目', conversation.meta.project_name || '独立聊天'], ['工作目录', snapshot?.current_context?.workspace], ['模型', snapshot?.current_context?.model], ['专家', snapshot?.current_context?.conversation_agent?.name || 'DeepSeek Cowork'], ['任务方式', snapshot?.current_context?.mode || '普通任务'], ['能力', snapshot?.current_context?.skills?.join('、') || '自动选择']].map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text || '未设置'}</dd></div>)}</dl>
        {disclosure('selected-references', '引用与资料', {conversations: snapshot?.current_context?.conversation_refs || [], knowledge: snapshot?.current_context?.knowledge_refs || []})}
        <div className="context-heading"><strong>{historical ? '该轮任务提交' : '本轮任务提交'}</strong>{snapshot?.submitted_context && copyButton('submitted-context', stringify(snapshot.submitted_context))}</div>
        {snapshot?.submitted_context ? <>{disclosure('submitted-context', '查看完整提交上下文', snapshot.submitted_context)}<p className="muted">来自{historical ? '该轮' : '本轮'}已接收的提交记录，当前选择将在下次任务生效。</p></> : <p className="muted">还没有提交任务。</p>}
        <div className="context-heading"><strong>{historical ? '该轮最近模型请求' : '最近模型请求'}</strong>{request && copyButton('request-context', stringify(request))}<button className="text-link" disabled={requestBusy} onClick={() => void readRequest()}>{requestBusy ? '读取中…' : request ? '刷新' : '查看完整请求'}</button></div>
        {requestError && <p className="error-text" role="alert">{requestError}</p>}{request && <>{disclosure('request-context', '请求原文', request, true)}<p className="muted">{request.scope}</p></>}
      </div>}
    </div>
    {value.agentId && selectedAgent && !childReadOnly && <div className="child-composer"><textarea aria-label="补充子任务" placeholder="给这个子任务补充要求…" value={draft.text} onChange={event => updateDraft({...draft, text: event.target.value})}/><div><span>{statuses[selectedAgent.status] || selectedAgent.status}</span>{childRunning ? <button className="icon-button" title="停止子任务" disabled={busy || selectedAgent.status === 'stopping'} onClick={() => void agentAction('agent.stop')}><Square size={14}/></button> : selectedAgent.status === 'interrupted' && <button className="secondary" disabled={busy} onClick={() => void agentAction('agent.resume')}><Play size={13}/>继续</button>}<button className="send-button" title={draft.messageId ? '重试原补充' : '发送给子任务'} disabled={busy || !draft.text.trim() && !draft.messageId} onClick={() => void agentAction('agent.send')}><ArrowUp size={17}/></button></div></div>}
  </div>;
}
