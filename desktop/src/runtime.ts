import type { Message, RuntimeEvent } from './types';

export function messageText(content: any): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.filter(p => p?.type === 'text' || p?.type === 'output_text').map(p => p.text || '').join('\n');
  return '';
}
export function unwrapEvents(events: any[]): RuntimeEvent[] {
  return events.map(event => event.payload ? { ...event.payload, sequence: event.sequence } : event).filter(event => typeof event.type === 'string');
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
    const message = {...(live.get(id)||{ id, role: 'assistant', content: '', reasoning_content: '', tool_calls: [], live:false, meta: { turn_id: event.run_id } })};
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
  return result.filter(m => messageText(m.content) || m.reasoning_content || m.tool_calls?.length || m.role === 'tool' || m.meta?.desktop_attachments?.length || m.meta?.user_added_files?.length);
  }
}
export const isActive = (status?: string) => ['queued', 'running', 'stopping'].includes(status || '');

export type MessageGroup = {id: string; messages: Message[]; assistant: boolean};
/** Presentation groups preserve every message and guidance boundary in ledger order. */
export function messageGroups(messages: Message[]): MessageGroup[] {
  const groups: MessageGroup[] = [];
  for (const message of messages) {
    const assistant = ['assistant', 'tool'].includes(message.role), previous = groups.at(-1);
    const turn = message.meta?.turn_id || message.meta?.ui_turn_id;
    const priorTurn = previous?.messages[0].meta?.turn_id || previous?.messages[0].meta?.ui_turn_id;
    if (assistant && previous?.assistant && (!turn || !priorTurn || turn === priorTurn)) previous.messages.push(message);
    else groups.push({id: message.id, assistant, messages: [message]});
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

export function runErrorSummary(error: string) {
  if (/Image generation is not enabled/i.test(error)) return '模型服务未开放请求所需的图片生成能力。请核对该服务的模型能力配置，或选择其他已配置模型后继续。';
  if (/\b(401|403)\b|permission_error|authentication/i.test(error)) return '模型服务拒绝了请求。请核对 API Key、模型权限和服务配置后再试。';
  if (/\b429\b|rate.limit/i.test(error)) return '模型服务暂时限流。请稍后继续，或在输入区选择其他已配置模型。';
  if (/timeout|timed.out|connection|network/i.test(error)) return '与模型服务的连接未能完成。请检查网络和接口地址后继续。';
  return '任务未能完成。已发送的消息和已保存输出仍保留，可以补充要求后继续；具体原因见详情。';
}
