import { useEffect, useId, useMemo, useState, type Ref } from 'react';
import { ChartColumn, ChevronRight, X } from 'lucide-react';
import type { RuntimeEvent, Settings } from './types';
import { generationRates, SPEED_TREND_SECONDS, UsageProjection, type SpeedRequest } from './usageMetrics';
import './usage.css';

const format = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format;
const groups = [
  ['对话消息', ['user', 'assistant']],
  ['工具结果', ['results']],
  ['指令与技能', ['system', 'injected']],
  ['工具定义', ['tools']],
] as const;
const percent = (value: number, sum: number) => sum > 0 ? value / sum * 100 : 0;

function SpeedTrend({ rates, reference }: { rates: (number | undefined)[]; reference?: number }) {
  const width = 320, height = 64;
  const peak = Math.max(reference ?? 0, ...rates.map(rate => rate ?? 0));
  const step = peak <= 20 ? 5 : 20;
  const max = Math.max(step, Math.ceil(peak / step) * step);
  const y = (value: number) => height * (1 - value / max);
  const points = rates.flatMap((rate, i) => rate == null ? [] : [[i * width / SPEED_TREND_SECONDS, y(rate)]]);
  const line = points.map(point => point.join(',')).join(' ');
  const [endX, endY] = points[points.length - 1];

  return <div className="usage-trend" role="img" aria-label={`近 ${SPEED_TREND_SECONDS} 秒的生成速度走势${reference == null ? '' : '，虚线为上次完成均速'}`}>
    <div className="usage-trend-scale" aria-hidden="true"><span>{max}</span><span>{max / 2}</span><span>0</span></div>
    <svg viewBox="-4 -4 328 72" preserveAspectRatio="none" aria-hidden="true">
      <polygon className="usage-trend-area" points={`${points[0][0]},${height} ${line} ${endX},${height}`}/>
      {[0, height / 2, height].map(level => <line key={level} className="usage-trend-grid" x1={0} y1={level} x2={width} y2={level}/>)}
      {reference != null && <line className="usage-trend-reference" x1={0} y1={y(reference)} x2={width} y2={y(reference)}/>}
      <polyline className="usage-trend-line" points={line}/>
      <circle className="usage-trend-dot" cx={endX} cy={endY} r={3}/>
    </svg>
    <div className="usage-trend-time" aria-hidden="true"><span>{SPEED_TREND_SECONDS} 秒前</span><span>{SPEED_TREND_SECONDS / 2} 秒前</span><span>现在</span></div>
  </div>;
}

function RateValue({ value }: { value?: number }) {
  return <div className="usage-rate-value"><strong>{value == null ? '—' : value.toFixed(1)}</strong>{value != null && <small>tok/s</small>}</div>;
}

function useGenerationRates(request?: SpeedRequest) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const measuring = request?.firstOutputAt != null;
  useEffect(() => {
    if (!measuring) return;
    const update = () => setNow(Date.now() / 1000);
    update();
    const timer = window.setInterval(update, 500);
    return () => window.clearInterval(timer);
  }, [measuring]);
  return generationRates(request, now);
}

function useUsage(events: RuntimeEvent[], activeRunId?: string) {
  const [projection] = useState(() => new UsageProjection());
  return useMemo(() => projection.project(events, activeRunId), [projection, events, activeRunId]);
}

export function UsageToolbarButton({ events, activeRunId, showRate, open, onClick, buttonRef, iconOnly = false }: {
  events: RuntimeEvent[]; activeRunId?: string; showRate?: boolean; open: boolean; iconOnly?: boolean;
  onClick: () => void; buttonRef?: Ref<HTMLButtonElement>;
}) {
  const usage = useUsage(events, showRate ? activeRunId : undefined);
  const rate = useGenerationRates(usage.request).at(-1);
  return <button ref={buttonRef} className={(iconOnly ? 'icon-button' : 'toolbar-button') + ' usage-toolbar-button'}
    title={iconOnly ? undefined : '用量统计'} aria-label="用量统计"
    aria-haspopup="dialog" aria-expanded={open} onClick={onClick}><ChartColumn size={16}/>{!iconOnly && <span>用量统计</span>}
    {rate != null && <small className="usage-toolbar-rate">{rate.toFixed(1)} tok/s</small>}</button>;
}

function GenerationSpeed({ request, lastSpeed }: { request?: SpeedRequest; lastSpeed?: number }) {
  const measuring = request?.firstOutputAt != null;
  const rates = useGenerationRates(request);
  const rate = rates.at(-1);

  return <section className="usage-speed">
    <div className="usage-heading"><h4>生成速度</h4>{request && <small className="usage-live">{measuring ? '生成中' : '等待输出'}</small>}</div>
    {!request && lastSpeed == null ? <p className="usage-empty">暂无速度记录</p> : <div className="usage-rates">
      {request && <div className="usage-rate-live">
        <RateValue value={rate}/><small>近 3 秒滚动估算</small>
      </div>}
      {lastSpeed != null && <div className={request ? 'usage-rate-previous' : ''}>
        <RateValue value={lastSpeed}/><small>{rate != null && <i className="usage-reference-key" aria-hidden="true"/>}上次完成均速</small>
      </div>}
    </div>}
    {rate != null && <SpeedTrend rates={rates} reference={lastSpeed}/>}
  </section>;
}

export default function Usage({ events, activeRunId, settings, onSave, onClose }: {
  events: RuntimeEvent[];
  activeRunId?: string;
  settings: Pick<Settings, 'context_policy'>;
  onSave?: (target: number) => Promise<void>;
  onClose: () => void;
}) {
  const usage = useUsage(events, activeRunId);
  const targetId = useId();
  const saved = settings.context_policy.enabled ? settings.context_policy.target_tokens : 0;
  const [target, setTarget] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const parts = groups.map(([label, keys], index) => ({
    label, index, value: keys.reduce((sum, key) => sum + Math.max(0, usage.context?.counts?.[key] || 0), 0),
  }));
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  const capacity = usage.capacity?.effective_tokens || 0;
  const used = Math.max(0, usage.context?.total || 0);
  const usedPercent = percent(used, capacity);
  const cachePercent = usage.cacheInput ? Math.round(percent(usage.cached, usage.cacheInput)) : undefined;
  const hasContext = usage.context?.status === 'available';

  async function save() {
    if (!onSave) return;
    setBusy(true);
    setError('');
    try {
      await onSave(target);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <section className="usage-popover" role="dialog" aria-label="用量概览">
    <div className="usage-heading usage-title"><h3>用量</h3><button autoFocus className="icon-button" title="关闭用量" aria-label="关闭用量" onClick={onClose}><X size={16}/></button></div>
    <GenerationSpeed request={usage.request} lastSpeed={usage.lastSpeed}/>

    <section className="usage-section">
      <div className="usage-heading"><h4>对话累计</h4><small>{usage.count} 次请求有用量记录</small></div>
      <div className="usage-metrics">
        <div>
          <span>输入与输出</span>
          <div className="usage-total"><strong>{usage.count ? format(usage.input + usage.output) : '—'}</strong><small>Token</small></div>
        </div>
        <div className="usage-cache">
          <div className="usage-cache-ring" style={cachePercent == null ? undefined : {
            background: `conic-gradient(var(--usage-accent) ${cachePercent}%, var(--soft) 0)`,
          }}><strong>{cachePercent == null ? '—' : `${cachePercent}%`}</strong></div>
          <div><span>输入缓存命中</span><small>仅计有缓存数据的请求</small></div>
        </div>
      </div>
    </section>

    <section className="usage-section">
      <div className="usage-heading"><h4>当前上下文</h4>{hasContext && <small>文本估算{usage.context?.has_media ? ' · 媒体未计入' : ''}</small>}</div>
      {hasContext ? <>
        <div className="usage-budget">
          <div><strong>{format(used)}</strong><small>{capacity > 0 ? `/ ${format(capacity)} Token` : 'Token'}</small></div>
          <small>{capacity > 0 ? <>占用 <b>{usedPercent.toFixed(1)}%</b></> : '预算未知'}</small>
        </div>
        {capacity > 0 && <div className="usage-capacity" role="meter" aria-label="上下文占用" aria-valuemin={0} aria-valuemax={capacity}
          aria-valuenow={Math.min(used, capacity)} aria-valuetext={`${format(used)} / ${format(capacity)} Token`}>
          <span style={{ width: Math.min(100, usedPercent) + '%' }}/>
        </div>}
        <p className="usage-composition-title">文本组成</p>
        <div className="usage-composition" aria-hidden="true">{parts.map(part =>
          <span key={part.label} className={`usage-part-${part.index}`} style={{ width: percent(part.value, total) + '%' }}/>)}</div>
        <ul className="usage-groups" aria-label="文本组成与占比">{parts.map(part =>
          <li key={part.label}>
            <span><i className={`usage-part-${part.index}`} aria-hidden="true"/>{part.label}</span>
            <span>{format(part.value)}</span>
            <small>{percent(part.value, total).toFixed(1)}%</small>
          </li>)}</ul>
      </> : <>
        <p className="usage-empty">{usage.context ? '暂无文本估算' : '首次请求发出后显示上下文用量'}</p>
        {capacity > 0 && <small>本轮有效上限 {format(capacity)} Token</small>}
      </>}
    </section>

    {onSave && <section className="usage-section">
      <div className="usage-heading usage-limit">
        <div><label htmlFor={targetId}>目标上限</label><small id={targetId+'-note'}>下次开始任务时生效</small></div>
        <select id={targetId} aria-describedby={targetId+'-note'} value={target} disabled={busy}
          onChange={event => { setTarget(Number(event.target.value)); setError(''); }}>
          <option value={0}>未启用</option><option value={300000}>300K</option><option value={1000000}>1M</option>
        </select>
      </div>
      {target !== saved && <div className="button-row">
        <button className="secondary" disabled={busy} onClick={() => { setTarget(saved); setError(''); }}>取消</button>
        <button className="primary" disabled={busy} onClick={save}>{busy ? '正在保存…' : '保存'}</button>
      </div>}
      {error && <p className="error-text" role="alert">{error}</p>}
    </section>}

    <details className="usage-section usage-details">
      <summary><ChevronRight size={14} aria-hidden="true"/>统计口径与详情</summary>
      <p className="muted">生成速度根据收到的思考和正文片段估算。请求用量由模型服务返回，不同模型的请求合计展示。上下文为最近一次请求的文本估算。</p>
      <pre>{JSON.stringify({ input_tokens: usage.input, output_tokens: usage.output, last_request: usage.last ?? null, capacity: usage.capacity ?? null }, null, 2)}</pre>
    </details>
  </section>;
}
