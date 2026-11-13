import type { Message, Run, RuntimeEvent } from './types';

export function messageText(content: any): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.filter(p => p?.type === 'text' || p?.type === 'output_text').map(p => p.text || '').join('\n');
  return '';
}
export function unwrapEvents(events: any[]): RuntimeEvent[] {
  return events.map(event => event.payload ? { ...event.payload, sequence: event.sequence, created_at: event.payload.created_at ?? event.created_at } : event).filter(event => typeof event.type === 'string');
}
function messageDate(createdAt?: number) {
  if (typeof createdAt !== 'number' || !Number.isFinite(createdAt) || createdAt <= 0) return null;
  const date = new Date(createdAt * 1000);
  return Number.isFinite(date.getTime()) ? date : null;
}
export function messageTime(createdAt?: number) {
  const date = messageDate(createdAt);
  if (!date) return null;
  return {iso: date.toISOString(), label: date.toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}),
    full: date.toLocaleString('zh-CN', {year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'})};
}
type TimeInterval = {start: number; end?: number};
export type TurnTiming = {id: string; start?: number; end?: number; thinking: Record<string, TimeInterval[]>};
/** Measure recorded stream boundaries; missing history never starts a new clock. */
export function turnTimings(events: RuntimeEvent[]) {
  const turns = new Map<string, TurnTiming>(), thinking = new Map<string, {source: string; interval: TimeInterval}>();
  for (const event of events) {
    if (!event.run_id || !messageDate(event.created_at)) continue;
    const time = event.created_at!;
    const turn = turns.get(event.run_id) || {id:event.run_id, thinking:{}};
    turns.set(event.run_id, turn);
    if (event.type === 'turn_started') turn.start ??= time;
    if (event.type === 'final') turn.end ??= time;
    const source = event.source_message_id || '';
    const open = thinking.get(event.run_id);
    if (open && (['content', 'content_snapshot', 'tool_call', 'tool_result', 'final'].includes(event.type) || event.type === 'thinking' && source !== open.source)) {
      if (time >= open.interval.start) open.interval.end = time;
      thinking.delete(event.run_id);
    }
    if (event.type === 'thinking' && source && !thinking.has(event.run_id)) {
      const interval = {start:time};
      (turn.thinking[source] ||= []).push(interval);
      thinking.set(event.run_id, {source, interval});
    }
  }
  return turns;
}
export function elapsedTime(start?: number, end?: number) {
  return typeof start === 'number' && typeof end === 'number' && Number.isFinite(start) && Number.isFinite(end) && end >= start ? end - start : undefined;
}
export function thinkingTime(timing: TurnTiming | undefined, source: string, now?: number) {
  const intervals = timing?.thinking[source];
  if (!intervals?.length) return undefined;
  let total = 0;
  for (const interval of intervals) {
    const seconds = elapsedTime(interval.start, interval.end ?? now);
    if (seconds == null) return undefined;
    total += seconds;
  }
  return total;
}
export function formatDuration(seconds: number) {
  if (seconds < .1) return '<0.1 秒';
  return seconds < 60 ? `${seconds.toFixed(1)} 秒` : `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`;
}
export function mergeEvents(previous: RuntimeEvent[], incoming: RuntimeEvent[]): RuntimeEvent[] {
  const key = (e: RuntimeEvent) => `${e.run_id}:${e.sequence}`;
  const seen = new Set(previous.filter(e => e.sequence).map(key));
  const added=incoming.filter(e => { if (!e.sequence) return true; if (seen.has(key(e))) return false; seen.add(key(e)); return true; });
  return added.length?[...previous,...added]:previous;
}
export function displayMessages(saved: Message[], events: RuntimeEvent[], activeRun?: string): Message[] {
  return new MessageProjection().project(saved,events,activeRun);
}
/** Reuse unaffected message objects and process only newly appended events. */
export class MessageProjection {
  private previous:RuntimeEvent[]=[];
  private fragments=new Map<string,Message>();
  project(saved:Message[],events:RuntimeEvent[],activeRun?:string):Message[]{
  if(events.length<this.previous.length||this.previous.length&&(events[0]!==this.previous[0]||events[this.previous.length-1]!==this.previous.at(-1))){
    this.previous=[];this.fragments.clear();
  }
  const messages = saved.filter(m => !m.meta?.hidden && !m.meta?.ui_hidden && !m.meta?.context_hidden
    && !['system', 'developer'].includes(m.role) && !['skill_context', 'skill_context_update', 'skill_state_update', 'runtime_context'].includes(m.meta?.kind));
  const ids = new Set(messages.map(m => m.id));
  const toolIds = new Set(messages.filter(m => m.role === 'tool').map(m => m.tool_call_id));
  const live = this.fragments;
  for (const event of events.slice(this.previous.length)) {
    if (!['content', 'content_snapshot', 'thinking', 'tool_call', 'tool_result'].includes(event.type)) continue;
    if (event.type === 'tool_result') {
      const data = event.data || {};
      if (!data.id) continue;
      const id = `tool:${event.run_id}:${data.id}`;
      live.set(id, {id, role: 'tool', tool_call_id: data.id, content: data.result || '', result_obj: data.result_obj,
        meta: {turn_id: event.run_id, tool_name: data.name}});
      continue;
    }
    const id = event.source_message_id || `live-${event.run_id || ''}`;
    const message: Message = {...(live.get(id)||{ id, role: 'assistant', content: '', reasoning_content: '', tool_calls: [], live:false, meta: { turn_id: event.run_id } })};
    if (!messageDate(message.created_at) && messageDate(event.created_at)) message.created_at = event.created_at;
    if (event.type === 'content') message.content += event.delta || '';
    if (event.type === 'content_snapshot') message.content = event.content || '';
    if (event.type === 'thinking') message.reasoning_content += event.delta || '';
    if (event.type === 'tool_call' && event.data) message.tool_calls=[...(message.tool_calls||[]),event.data];
    live.set(id,message);
  }
  this.previous=events;
  const visible=[...live.values()].filter(message=>!ids.has(message.id)&&!(message.role==='tool'&&toolIds.has(message.tool_call_id)));
  const streaming = visible.filter(message => activeRun && message.meta?.turn_id===activeRun && message.role === 'assistant');
  const last=streaming.at(-1)?.id;
  for(const message of visible){const active=message.id===last;if(Boolean(message.live)!==active)live.set(message.id,{...message,live:active});}
  // Journal fragments belong before the next turn. Keeping every run's durable
  // events preserves interrupted output after the user sends another message.
  const result = [...messages];
  for (const original of visible) {
    const message=live.get(original.id)!;
    const run = message.meta?.turn_id;
    const start = result.findIndex(m => m.role === 'user' && m.meta?.turn_id === run);
    let next = start < 0 ? -1 : result.findIndex((m, i) => i > start && m.role === 'user' && m.meta?.turn_id !== run);
    if (next < 0) next = result.length;
    result.splice(next, 0, message);
  }
  return interleaveGuidance(result, events).filter(m => messageText(m.content) || m.reasoning_content || m.tool_calls?.length || m.role === 'tool' || m.meta?.desktop_attachments?.length || m.meta?.user_added_files?.length);
  }
}
export const isActive = (status?: string) => ['queued', 'running', 'stopping'].includes(status || '');

const messageRun = (message: Message) => message.meta?.turn_id || message.meta?.ui_turn_id;
const guidanceTime = (message: Message) => message.meta?.ui_submitted_at || message.created_at;
const isGuidance = (message: Message) => message.role === 'user' && (message.meta?.same_turn_guidance || message.meta?.kind === 'user_guidance');

/** Presentation only: receipt time divides streamed stages; provider history stays intact. */
export function interleaveGuidance(messages: Message[], events: RuntimeEvent[]): Message[] {
  const runs = new Set(messages.filter(isGuidance).map(messageRun).filter(Boolean));
  if (!runs.size) return messages;
  const result = [...messages];
  for (const run of runs) {
    const entries = result.filter(message => messageRun(message) === run);
    const guidance = entries.filter(message => isGuidance(message) && messageDate(guidanceTime(message)))
      .sort((a, b) => guidanceTime(a) - guidanceTime(b));
    // Qt histories already contain explicit display anchors around guidance.
    if (!guidance.length || entries.some(message => message.meta?.ui_visible_fragment)) continue;
    const bucket = (time: number) => guidance.filter(message => guidanceTime(message) <= time).length;
    const sourceEvents = new Map<string, RuntimeEvent[]>(), toolTimes = new Map<string, number>();
    for (const event of events) {
      if (event.run_id !== run || !messageDate(event.created_at)) continue;
      if (event.type === 'tool_result' && event.data?.id) toolTimes.set(event.data.id, event.created_at!);
      if (event.source_message_id && ['content', 'content_snapshot', 'thinking', 'tool_call'].includes(event.type)) {
        const list = sourceEvents.get(event.source_message_id) || [];
        list.push(event); sourceEvents.set(event.source_message_id, list);
      }
    }
    const ordered: {message: Message; time: number; order: number}[] = [];
    for (const [order, message] of entries.entries()) {
      if (isGuidance(message)) {ordered.push({message, time:guidanceTime(message), order}); continue;}
      if (message.role === 'user') {ordered.push({message, time:-Infinity, order}); continue;}
      const stream = sourceEvents.get(message.meta?.ui_source_message_id || message.id) || [];
      const first = stream[0]?.created_at ?? toolTimes.get(message.tool_call_id || '') ?? message.created_at;
      if (!stream.length || message.role !== 'assistant') {
        ordered.push({message, time:first ?? Infinity, order}); continue;
      }
      const pieces = new Map<number, {content:string; thinking:string; calls:any[]; time:number}>();
      let text = '', thoughts = '', valid = true;
      for (const event of stream) {
        const part = bucket(event.created_at!);
        const piece = pieces.get(part) || {content:'', thinking:'', calls:[], time:event.created_at!};
        if (event.type === 'content_snapshot') {
          if (!(event.content || '').startsWith(text)) {valid = false; break;}
          piece.content += (event.content || '').slice(text.length); text = event.content || '';
        } else if (event.type === 'content') {piece.content += event.delta || ''; text += event.delta || '';}
        else if (event.type === 'thinking') {piece.thinking += event.delta || ''; thoughts += event.delta || '';}
        else if (event.type === 'tool_call' && event.data) piece.calls.push(event.data);
        pieces.set(part, piece);
      }
      // Incomplete event pages or corrected snapshots cannot replace saved text.
      if (!valid || text !== messageText(message.content) || thoughts !== (message.reasoning_content || '') ||
          (message.tool_calls || []).some(call => ![...pieces.values()].some(piece => piece.calls.some(item => item.id === call.id)))) {
        ordered.push({message, time:first ?? Infinity, order}); continue;
      }
      const last = [...pieces.keys()].at(-1);
      for (const [part, piece] of pieces) {
        const beforeGuidance = part < guidance.length;
        ordered.push({time:piece.time, order, message:{...message,
          id:beforeGuidance ? `${message.id}:before:${guidance[part].id}` : message.id,
          content:piece.content, reasoning_content:piece.thinking,
          tool_calls:piece.calls.map(call => message.tool_calls?.find(item => item.id === call.id) || call),
          live:message.live && !beforeGuidance,
          meta:{...message.meta, ui_source_message_id:message.meta?.ui_source_message_id || message.id,
            ...(pieces.size > 1 ? {ui_split_reasoning:true} : {}),
            ...(beforeGuidance ? {ui_reply_kind:'stage'} : {}),
            ...(part !== last ? {desktop_attachments:[], user_added_files:[]} : {})}}});
      }
    }
    const sorted = ordered.sort((a, b) => a.time - b.time || a.order - b.order).map(item => item.message);
    const positions = result.flatMap((message, index) => messageRun(message) === run ? [index] : []);
    // Never move a fragment across another conversation turn.
    if (positions.at(-1)! - positions[0] + 1 !== positions.length) continue;
    result.splice(positions[0], positions.length, ...sorted);
  }
  return result;
}

export type QuestionRecord = {request:any; outcome?:any; runId?:string; sourceId?:string; callId?:string};
export function interactionRecords(events: RuntimeEvent[], messages: Message[] = []): QuestionRecord[] {
  const records = new Map<string, QuestionRecord>(), outcomes = new Map<string, any>();
  for (const event of events) {
    if (event.type === 'interaction_request' && event.data?.request_id && !records.has(event.data.request_id))
      records.set(event.data.request_id, {request:event.data, runId:event.run_id,
        sourceId:event.source_message_id, callId:event.data.metadata?.tool_call_id});
    if (event.type === 'interaction_finished' && event.data?.request_id) outcomes.set(event.data.request_id, event.data);
  }
  const responses = new Map<string, any>();
  for (const value of [...events.filter(event => event.type === 'tool_result').map(event => event.data?.result_obj), ...messages.map(message => message.result_obj)]) {
    const response = value?.interaction_response;
    if (response?.request_id) responses.set(response.request_id, response);
  }
  return [...records].map(([id, record]) => {
    const outcome = outcomes.get(id), response = outcome?.response || responses.get(id);
    return {...record, outcome: outcome || response ? {...outcome, status:outcome?.status || response?.status,
      answered:outcome?.answered ?? response?.status === 'completed', response} : undefined};
  });
}

export type MessageGroup = {id: string; messages: Message[]; assistant: boolean; questions?: QuestionRecord[]};
/** Presentation groups preserve every message and guidance boundary in ledger order. */
export function messageGroups(messages: Message[], questions: QuestionRecord[] = []): MessageGroup[] {
  const groups: MessageGroup[] = [];
  for (const message of messages) {
    const assistant = ['assistant', 'tool'].includes(message.role), previous = groups.at(-1);
    const turn = message.meta?.turn_id || message.meta?.ui_turn_id;
    const priorTurn = previous?.messages[0].meta?.turn_id || previous?.messages[0].meta?.ui_turn_id;
    if (assistant && previous?.assistant && (!turn || !priorTurn || turn === priorTurn)) previous.messages.push(message);
    else groups.push({id: message.id, assistant, messages: [message]});
  }
  for (const question of questions) {
    const candidates = groups.filter(group => group.assistant && group.messages.some(message => messageRun(message) === question.runId));
    let group = candidates.find(group => group.messages.some(message =>
      question.callId && (message.tool_call_id === question.callId || message.tool_calls?.some(call => call.id === question.callId))));
    group ||= [...candidates].reverse().find(group => group.messages.some(message =>
      question.sourceId && (message.id === question.sourceId || message.meta?.ui_source_message_id === question.sourceId)));
    group ||= candidates.at(-1);
    if (!group) {
      group = {id:'interaction:' + question.request.request_id, assistant:true, messages:[{
        id:'interaction:' + question.request.request_id, role:'assistant', content:'', meta:{turn_id:question.runId}}]};
      const index = groups.map(item => item.messages.some(message => messageRun(message) === question.runId)).lastIndexOf(true);
      groups.splice(index < 0 ? groups.length : index + 1, 0, group);
    }
    (group.questions ||= []).push(question);
  }
  return groups;
}

export function replyParts(messages: Message[]) {
  const last = [...messages].reverse().find(message => message.role === 'assistant' &&
    (messageText(message.content) || message.meta?.desktop_attachments?.length || message.meta?.user_added_files?.length));
  // A stage that calls a tool remains in the process. Final text is always outside its disclosure.
  const answer = last && !last.tool_calls?.length && last.meta?.ui_reply_kind !== 'stage' ? last : undefined;
  const results = new Map(messages.filter(message => message.role === 'tool').map(message => [message.tool_call_id, message]));
  return {answer, results, hasProcess: messages.some(message => message.role === 'tool' || message.reasoning_content || message.tool_calls?.length || message !== answer && messageText(message.content))};
}

export function isUserStopped(run?: Pick<Run, 'status' | 'error'> | null) {
  return run?.status === 'interrupted' && /^(?:run )?interrupted by user\.?$/i.test(run.error.trim());
}

export function runErrorSummary(error: string) {
  if (/Image generation is not enabled/i.test(error)) return '模型服务未开放请求所需的图片生成能力。请核对该服务的模型能力配置，或选择其他已配置模型后继续。';
  if (/\b(401|403)\b|permission_error|authentication/i.test(error)) return '模型服务拒绝了请求。请核对 API Key、模型权限和服务配置后再试。';
  if (/\b429\b|rate.limit/i.test(error)) return '模型服务暂时限流。请稍后继续，或在输入区选择其他已配置模型。';
  if (/timeout|timed.out|connection|network/i.test(error)) return '与模型服务的连接未能完成。请检查网络和接口地址后继续。';
  return '任务未能完成。已发送的消息和已保存输出仍保留，可以补充要求后继续；具体原因见详情。';
}
