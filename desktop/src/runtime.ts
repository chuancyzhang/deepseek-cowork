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
  return [...previous, ...incoming.filter(e => { if (!e.sequence) return true; if (seen.has(key(e))) return false; seen.add(key(e)); return true; })];
}
export function displayMessages(saved: Message[], events: RuntimeEvent[], activeRun?: string): Message[] {
  const messages = saved.filter(m => !m.meta?.hidden && !m.meta?.ui_hidden && !m.meta?.context_hidden
    && !['system', 'developer'].includes(m.role) && !['skill_context', 'skill_context_update', 'skill_state_update', 'runtime_context'].includes(m.meta?.kind));
  const ids = new Set(messages.map(m => m.id));
  const toolIds = new Set(messages.filter(m => m.role === 'tool').map(m => m.tool_call_id));
  const live = new Map<string, Message>();
  for (const event of events) {
    if (!['content', 'content_snapshot', 'thinking', 'tool_call', 'tool_result'].includes(event.type)) continue;
    if (event.type === 'tool_result') {
      const data = event.data || {};
      if (!data.id || toolIds.has(data.id)) continue;
      const id = `tool:${event.run_id}:${data.id}`;
      live.set(id, {id, role: 'tool', tool_call_id: data.id, content: data.result || '', result_obj: data.result_obj,
        meta: {turn_id: event.run_id, tool_name: data.name}});
      continue;
    }
    const id = event.source_message_id || `live-${event.run_id || ''}`;
    if (ids.has(id)) continue;
    if (!live.has(id)) live.set(id, { id, role: 'assistant', content: '', reasoning_content: '', tool_calls: [], live: event.run_id === activeRun, meta: { turn_id: event.run_id } });
    const message = live.get(id)!;
    if (event.type === 'content') message.content += event.delta || '';
    if (event.type === 'content_snapshot') message.content = event.content || '';
    if (event.type === 'thinking') message.reasoning_content += event.delta || '';
    if (event.type === 'tool_call' && event.data) message.tool_calls!.push(event.data);
  }
  const streaming = [...live.values()].filter(message => message.live && message.role === 'assistant');
  for (const message of streaming.slice(0, -1)) message.live = false;
  // Journal fragments belong before the next turn. Keeping every run's durable
  // events preserves interrupted output after the user sends another message.
  const result = [...messages];
  for (const message of live.values()) {
    const run = message.meta?.turn_id;
    const start = result.findIndex(m => m.role === 'user' && m.meta?.turn_id === run);
    let next = start < 0 ? -1 : result.findIndex((m, i) => i > start && m.role === 'user' && m.meta?.turn_id !== run);
    if (next < 0) next = result.length;
    result.splice(next, 0, message);
  }
  return result.filter(m => messageText(m.content) || m.reasoning_content || m.tool_calls?.length || m.role === 'tool');
}
export const isActive = (status?: string) => ['queued', 'running', 'stopping'].includes(status || '');

export function runErrorSummary(error: string) {
  if (/Image generation is not enabled/i.test(error)) return '模型服务未开放请求所需的图片生成能力。请核对该服务的模型能力配置，或选择其他已配置模型后继续。';
  if (/\b(401|403)\b|permission_error|authentication/i.test(error)) return '模型服务拒绝了请求。请核对 API Key、模型权限和服务配置后再试。';
  if (/\b429\b|rate.limit/i.test(error)) return '模型服务暂时限流。请稍后继续，或在输入区选择其他已配置模型。';
  if (/timeout|timed.out|connection|network/i.test(error)) return '与模型服务的连接未能完成。请检查网络和接口地址后继续。';
  return '任务未能完成。已发送的消息和已保存输出仍保留，可以补充要求后继续；具体原因见详情。';
}
