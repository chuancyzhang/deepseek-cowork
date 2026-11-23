import type {Message} from './types';

export const agentTools = new Set(['spawn_agent', 'send_input', 'wait_agent', 'close_agent', 'list_agents']);
export const agentStatuses: Record<string, string> = {queued:'排队中',running:'运行中',stopping:'正在停止',completed:'已完成',failed:'失败',incomplete:'未完成',interrupted:'已停止',idle:'待命',waiting:'等待中',closed:'已关闭',killed:'已结束',unknown:'状态未记录'};
export function objectValue(value: any): any {
  if (typeof value !== 'string') return value || {};
  try {return JSON.parse(value) || {};} catch {return {};}
}

// A display projection of recorded calls; never written back into model history.
export function agentRecords(messages: Message[], results: Map<string | undefined, Message>) {
  const names = new Map<string, string>();
  const calls = messages.flatMap(message => (message.tool_calls || []).map(tool => ({message,tool})));
  const known = new Set(calls.map(item => item.tool.id));
  messages.filter(message => message.role === 'tool' && !known.has(message.tool_call_id)).forEach(message => calls.push({message,tool:{id:message.tool_call_id,name:message.meta?.tool_name}}));
  const records = calls.filter(({tool}) => agentTools.has(tool.function?.name || tool.name));
  for (const {tool} of records) {
    const result = results.get(tool.id);
    const data = objectValue(result?.result_obj ?? result?.content);
    const args = objectValue(tool.function?.arguments ?? tool.arguments ?? tool.args);
    if (data.agent_id && (data.name || args.name)) names.set(data.agent_id, data.name || args.name);
    for (const agent of [...(data.agents || []), ...(data.completed || []), ...(data.pending || []), ...(data.agent ? [data.agent] : [])]) {
      if (agent.id && agent.name) names.set(agent.id, agent.name);
    }
  }
  return records.flatMap(({message,tool}) => {
    const name = tool.function?.name || tool.name;
    const result = results.get(tool.id);
    const data = objectValue(result?.result_obj ?? result?.content);
    const args = objectValue(tool.function?.arguments ?? tool.arguments ?? tool.args);
    const failed = Boolean(data.error || ['error','failed'].includes(data.status));
    const agents = [...(data.completed || []), ...(data.pending || [])];
    const targets: string[] = data.agent_id ? [data.agent_id] : data.agent?.id ? [data.agent.id] : agents.length ? agents.map(item=>item.id).filter(Boolean) : args.target ? [args.target] : Array.isArray(args.targets) ? args.targets : [];
    return (targets.length ? [...new Set(targets)] : ['']).map((target,index) => ({
      key:tool.id+':'+index, message, callId:tool.id, target:failed ? '' : target,
      name:names.get(target) || args.name || (target && !/^[a-f0-9]{32}$/i.test(target) ? target : ''),
      verb:({spawn_agent:'启动',send_input:'补充',wait_agent:'等待',close_agent:'结束',list_agents:'查看'} as Record<string,string>)[name],
      text:args.message || '', failed, error:data.error, received:Boolean(result),
    }));
  });
}

// Child journals hold tool activity separately from saved conversation messages.
export function childMessages(child: any): Message[] {
  const activity = child?.activity || [];
  return (child?.messages || []).filter((message: Message)=>['user','assistant','tool'].includes(message.role)).flatMap((message: Message) => {
    if (!message.id.endsWith(':assistant') || message.id.includes(':seed:')) return [message];
    const runId = message.id.split(':')[0];
    const steps: Message[] = [];
    const seen = new Set<string>();
    for (const event of activity) {
      const data = event.payload || event;
      if ((event.run_id || data.run_id) !== runId || !data.tool_call_id) continue;
      const key = data.event_type + ':' + data.tool_call_id;
      if (seen.has(key)) continue;
      seen.add(key);
      const meta = {turn_id:runId,tool_name:data.tool_name};
      if (data.event_type === 'tool_call') steps.push({id:key,role:'assistant',meta,tool_calls:[{id:data.tool_call_id,function:{name:data.tool_name,arguments:data.tool_args}}]});
      else if (data.event_type === 'tool_result') {
        let result = data.tool_result_obj;
        if (result == null) {try {result=JSON.parse(data.tool_result);} catch {result=undefined;}}
        steps.push({id:key,role:'tool',meta,tool_call_id:data.tool_call_id,content:data.tool_result,result_obj:result});
      }
    }
    const live = child.summary?.run_id === runId && ['running','queued','stopping'].includes(child.summary?.status);
    return [...(message.reasoning_content ? [{...message,id:message.id+':thinking',content:'',meta:{...message.meta,turn_id:runId},live}] : []), ...steps, {...message,reasoning_content:'',meta:{...message.meta,turn_id:runId},live}];
  });
}
