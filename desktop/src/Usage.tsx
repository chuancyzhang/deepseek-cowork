import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import type { RuntimeEvent, Settings } from './types';

export function summarizeUsage(events: RuntimeEvent[]) {
  const seen = new Set<string>();
  let input = 0, output = 0, cached = 0, cacheInput = 0, count = 0;
  let context: any = null, capacity: any = null, last: any = null;
  for (const event of events) {
    if (event.type !== 'observability') continue;
    const data = event.data || {};
    if (data.type === 'system_prompt') context = data.context_usage;
    if (data.type === 'context_capacity') capacity = data.capacity;
    if (data.type !== 'llm_usage' || !data.usage) continue;
    const usage = data.usage;
    const id = usage.request_id && `${event.run_id}:${usage.request_id}`;
    if (id && seen.has(id)) continue;
    if (id) seen.add(id);
    if (typeof usage.input_tokens !== 'number' || typeof usage.output_tokens !== 'number') continue;
    input += usage.input_tokens; output += usage.output_tokens; count++; last = usage;
    if (typeof usage.cached_input_tokens === 'number') { cached += usage.cached_input_tokens; cacheInput += usage.input_tokens; }
  }
  return { input, output, cached, cacheInput, count, context, capacity, last };
}

export default function Usage({ events, settings, onSave, onClose, visible }: {
  events: RuntimeEvent[]; settings: Settings; onSave: (target: number) => Promise<void>; onClose: () => void; visible: boolean;
}) {
  const usage = summarizeUsage(events);
  const saved = settings.context_policy.enabled ? settings.context_policy.target_tokens : 0;
  const [target, setTarget] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (!visible) return; const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); }; window.addEventListener('keydown', close); return () => window.removeEventListener('keydown', close); }, [onClose, visible]);
  const format = (value: number) => new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 0}).format(value);
  const groups = [['对话消息', ['user', 'assistant']], ['工具结果', ['results']], ['指令与技能', ['system', 'injected']], ['工具定义', ['tools']]] as const;
  return <section hidden={!visible} className="usage-popover" role="dialog" aria-label="用量概览">
    <div className="section-heading"><h3>用量</h3><button autoFocus className="icon-button" title="关闭用量" onClick={onClose}><X size={16}/></button></div>
    <p className="muted">当前对话 · 已返回用量的请求累计</p>
    <div className="usage-metrics"><div><small>输入与输出</small><strong>{usage.count ? format(usage.input + usage.output) : '—'}</strong><small>Token · {usage.count} 次请求</small></div><div><small>输入缓存命中</small><strong>{usage.cacheInput ? `${Math.round(usage.cached / usage.cacheInput * 100)}%` : '—'}</strong><small>仅统计提供缓存字段的请求</small></div></div>
    <div className="usage-section"><strong>当前上下文</strong>{usage.context?.status === 'available' ? <><p className="muted">最近一次请求的文本估算 · {format(usage.context.total)} Token{usage.context.has_media ? ' · 不含图片等媒体消耗' : ''}</p><div className="usage-groups">{groups.map(([label, keys]) => <div key={label}><span>{label}</span><span>{format(keys.reduce((sum, key) => sum + (usage.context.counts[key] || 0), 0))}</span></div>)}</div></> : <p className="muted">首次请求发出后显示，当前尚无统计。</p>}
    {usage.capacity?.effective_tokens && <p className="muted">本轮有效上限 {format(usage.capacity.effective_tokens)} Token</p>}</div>
    <div className="usage-section"><label className="usage-limit">目标上限<select aria-label="上下文目标上限" value={target} onChange={event => { setTarget(Number(event.target.value)); setError(''); }}><option value={0}>未启用</option><option value={300000}>300K</option><option value={1000000}>1M</option></select></label><p className="muted">下次开始任务时生效；当前运行和已有记录保持原样。</p>{target !== saved && <div className="button-row"><button className="secondary" disabled={busy} onClick={() => {setTarget(saved); setError('');}}>取消</button><button className="primary" disabled={busy} onClick={async () => { setBusy(true); try {await onSave(target);} catch (failure) {setError((failure as Error).message);} finally {setBusy(false);} }}>{busy ? '正在保存…' : '保存'}</button></div>}{error && <p role="alert">{error}</p>}</div>
    <details className="usage-section"><summary>统计口径与详情</summary><p className="muted">请求用量由模型服务返回；缺失时显示“—”。文本估算用于理解构成，不代表账单或精确的模型分词。不同模型的请求合计展示，不换算费用。</p><pre>{JSON.stringify({input_tokens: usage.input, output_tokens: usage.output, last_request: usage.last, capacity: usage.capacity}, null, 2)}</pre></details>
  </section>;
}
