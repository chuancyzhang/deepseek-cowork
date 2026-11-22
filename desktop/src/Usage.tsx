import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type Ref } from 'react';
import { ArrowLeft, ChartColumn, ChevronRight, Files, FileText, Gauge, Info, X } from 'lucide-react';
import type { Message, RuntimeEvent, Settings } from './types';
import { conversationFiles } from './attachments';
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

export type UsageView = 'overview' | 'details';

function SpeedTrend({ rates, reference, compact }: { rates: (number | undefined)[]; reference?: number; compact?: boolean }) {
  const width = 320, height = 64;
  const peak = Math.max(reference ?? 0, ...rates.map(rate => rate ?? 0));
  const step = peak <= 20 ? 5 : 20;
  const max = Math.max(step, Math.ceil(peak / step) * step);
  const y = (value: number) => height * (1 - value / max);
  const points = rates.flatMap((rate, i) => rate == null ? [] : [[i * width / SPEED_TREND_SECONDS, y(rate)]]);
  const line = points.map(point => point.join(',')).join(' ');
  const [endX, endY] = points[points.length - 1];

  return <div className={compact ? 'usage-sparkline' : 'usage-trend'} role="img"
    aria-label={`近 ${SPEED_TREND_SECONDS} 秒的生成速度走势${reference == null ? '' : '，虚线为上次完成均速'}`}>
    {!compact && <div className="usage-trend-scale" aria-hidden="true"><span>{max}</span><span>{max / 2}</span><span>0</span></div>}
    <svg viewBox="-4 -4 328 72" preserveAspectRatio="none" aria-hidden="true">
      <polygon className="usage-trend-area" points={`${points[0][0]},${height} ${line} ${endX},${height}`}/>
      {!compact && [0, height / 2, height].map(level => <line key={level} className="usage-trend-grid" x1={0} y1={level} x2={width} y2={level}/>)}
      {reference != null && <line className="usage-trend-reference" x1={0} y1={y(reference)} x2={width} y2={y(reference)}/>}
      <polyline className="usage-trend-line" points={line}/>
      <circle className="usage-trend-dot" cx={endX} cy={endY} r={3}/>
    </svg>
    {!compact && <div className="usage-trend-time" aria-hidden="true"><span>{SPEED_TREND_SECONDS} 秒前</span><span>{SPEED_TREND_SECONDS / 2} 秒前</span><span>现在</span></div>}
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

function useUsage(events: RuntimeEvent[], activeRunId?: string, turnId?: string) {
  const [projection] = useState(() => new UsageProjection());
  return useMemo(() => projection.project(events, activeRunId, turnId), [projection, events, activeRunId, turnId]);
}

export function UsageToolbarButton({ events, activeRunId, showRate, open, panelId, onClick, buttonRef, iconOnly = false }: {
  events: RuntimeEvent[]; activeRunId?: string; showRate?: boolean; open: boolean; iconOnly?: boolean;
  panelId: string; onClick: () => void; buttonRef?: Ref<HTMLButtonElement>;
}) {
  const usage = useUsage(events, showRate ? activeRunId : undefined);
  const rate = useGenerationRates(usage.request).at(-1);
  return <button ref={buttonRef} className={(iconOnly ? 'icon-button' : 'toolbar-button') + ' usage-toolbar-button'}
    title={iconOnly ? undefined : '用量统计'} aria-label="用量统计"
    aria-controls={open ? panelId : undefined} aria-expanded={open} onClick={onClick}><ChartColumn size={16} aria-hidden="true"/>{!iconOnly && <span>用量统计</span>}
    {rate != null && <small className="usage-toolbar-rate">{rate.toFixed(1)} tok/s</small>}</button>;
}

function GenerationSpeed({ request, lastSpeed, compact }: { request?: SpeedRequest; lastSpeed?: number; compact: boolean }) {
  const measuring = request?.firstOutputAt != null;
  const rates = useGenerationRates(request);
  const rate = rates.at(-1);

  if (compact) {
    const value = request ? rate : lastSpeed;
    return <section className="usage-compact-speed">
      <h4><Gauge size={14} aria-hidden="true"/>{!request && lastSpeed != null ? '上次均速' : '生成速度'}</h4>
      {rate != null && <SpeedTrend rates={rates} compact/>}
      {value != null ? <RateValue value={value}/> : <p className="usage-empty">
        {request ? measuring ? '采样中' : '等待输出' : '暂无速度记录'}
      </p>}
    </section>;
  }

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

export default function Usage({ id, events, activeRunId, turnId, messages, hasMoreHistory, settings, view, onView, onSave, onOpenFile, onClose }: {
  id: string;
  events: RuntimeEvent[];
  activeRunId?: string;
  turnId?: string;
  messages?: Message[];
  hasMoreHistory?: boolean;
  settings: Pick<Settings, 'context_policy'>;
  view: UsageView;
  onView: (view: UsageView) => void;
  onSave?: (target: number) => Promise<void>;
  onOpenFile?: (path: string) => void;
  onClose: () => void;
}) {
  const usage = useUsage(events, activeRunId, turnId);
  const files = useMemo(() => messages ? conversationFiles(messages) : [], [messages]);
  const [filesOpen, setFilesOpen] = useState(false);
  const targetId = useId();
  const pane = useRef<HTMLElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const reveal = useRef<HTMLButtonElement>(null);
  const previousView = useRef(view);
  const details = useRef<HTMLDetailsElement>(null);
  const expanded = view === 'details';
  useLayoutEffect(() => {
    if (previousView.current === view) return;
    previousView.current = view;
    pane.current?.scrollTo(0, 0);
    (expanded ? back : reveal).current?.focus({preventScroll:true});
  }, [expanded, view]);
  const saved = settings.context_policy.enabled ? settings.context_policy.target_tokens : 0;
  const [draftTarget, setDraftTarget] = useState<number>();
  const target = draftTarget ?? saved;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const parts = groups.map(([label, keys], index) => ({
    label, index, value: keys.reduce((sum, key) => sum + Math.max(0, usage.context?.counts?.[key] || 0), 0),
  }));
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  const budget = usage.capacity;
  const capacity = budget?.used_tokens == null ? 0 : budget.effective_tokens || 0;
  const used = Math.max(0, budget?.used_tokens ?? usage.context?.total ?? 0);
  const usedPercent = percent(used, capacity);
  const cachePercent = usage.cacheInput ? Math.round(percent(usage.cached, usage.cacheInput)) : undefined;
  const cacheStyle = cachePercent == null ? undefined : {
    background: `conic-gradient(var(--usage-accent) ${cachePercent}%, var(--soft) 0)`,
  };
  const cacheLabel = cachePercent == null ? '—' : `${cachePercent}%`;
  const totalLabel = hasMoreHistory ? '已载入累计' : '对话累计';
  const totalTokens = usage.count ? format(usage.input + usage.output) : '—';
  const hasComposition = usage.context?.status === 'available';
  const hasContext = hasComposition || capacity > 0;
  const current = activeRunId != null && usage.contextRunId === activeRunId;
  const excludedMedia = capacity > 0 ? budget?.unestimated_images : usage.context?.has_media;
  const budgetNote = budget?.target_tokens != null && capacity > 0 && capacity < budget.target_tokens
    ? '按模型容量计算，已扣除输出预留和安全余量。'
    : budget?.budget_source === 'target_setting' ? '模型容量未知，按目标估算。' : '';

  async function save() {
    if (!onSave) return;
    setBusy(true);
    setError('');
    try {
      await onSave(target);
      setDraftTarget(undefined);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <section id={id} ref={pane} className={expanded ? 'usage-popover' : 'usage-popover usage-overview'}
    aria-label={expanded ? '用量详情' : '用量概览'}>
    <div className="usage-heading usage-title">
      <div className="usage-title-start">
        {expanded && <button ref={back} className="icon-button" title="返回用量概览" aria-label="返回用量概览"
          onClick={() => onView('overview')}><ArrowLeft size={16} aria-hidden="true"/></button>}
        {!expanded && <ChartColumn size={15} aria-hidden="true"/>}
        <h3>{expanded ? '用量详情' : 'Token 用量'}</h3>
      </div>
      <button className="icon-button" title="关闭用量" aria-label="关闭用量" onClick={onClose}><X size={15} aria-hidden="true"/></button>
    </div>
    <GenerationSpeed request={usage.request} lastSpeed={usage.lastSpeed} compact={!expanded}/>

    {!expanded ? <>
      <dl className="usage-overview-metrics">
        <div><dt>本轮用量</dt><dd>{usage.turnTokens == null ? '—' : format(usage.turnTokens)}</dd></div>
        <div><dt>{totalLabel}</dt><dd>{totalTokens}</dd></div>
        <div><dt>缓存命中率</dt><dd className="usage-cache-value">{cacheLabel}{cachePercent != null &&
          <i className="usage-cache-indicator" style={cacheStyle} aria-hidden="true"/>}</dd></div>
      </dl>
      <div className="usage-overview-footer">
        {onOpenFile && files.length > 0 && <details className="usage-files" open={filesOpen}
          onToggle={event => {if (event.currentTarget.open !== filesOpen) setFilesOpen(event.currentTarget.open);}}>
          <summary aria-label={`文件，${files.length} 个`}><Files size={15} aria-hidden="true"/>文件
            <small aria-hidden="true">· {format(files.length)}</small><ChevronRight size={14} className="usage-files-chevron" aria-hidden="true"/>
          </summary>
          {filesOpen && <ul>{files.map(file => {
            const dot = file.name.lastIndexOf('.');
            const extension = dot > 0 ? file.name.slice(dot) : '';
            return <li key={file.path}><button title={file.path} aria-label={'打开文件 ' + file.name}
              onClick={() => onOpenFile(file.path)}><FileText size={15} aria-hidden="true"/>
              <span className="usage-file-name"><span>{extension ? file.name.slice(0, dot) : file.name}</span>{extension}</span>
            </button></li>;
          })}</ul>}
        </details>}
        <button className="usage-reveal" ref={reveal} onClick={() => onView('details')}>
          详情 <ChevronRight size={14} aria-hidden="true"/>
        </button>
      </div>
    </> : <>
      <section className="usage-section">
        <div className="usage-heading"><h4>{totalLabel}</h4><small>{usage.count} 次请求有用量记录</small></div>
        <div className="usage-metrics">
          <div>
            <span>输入与输出</span>
            <div className="usage-total"><strong>{totalTokens}</strong><small>Token</small></div>
          </div>
          <div className="usage-cache">
            <div className="usage-cache-ring" style={cacheStyle}><strong>{cacheLabel}</strong></div>
            <div><span>缓存命中率</span><small>仅计有缓存数据的请求</small></div>
          </div>
        </div>
      </section>

      <section className="usage-section">
        <div className="usage-heading"><h4>{hasContext ? current ? '本次上下文' : '上次请求' : '上下文'}</h4>
          {hasContext && <small>{capacity > 0 ? '估算' : '文本估算'}{excludedMedia ? ' · 部分媒体未计入' : ''}</small>}
        </div>
        {hasContext ? <>
          <div className="usage-budget">
            <div><strong>{format(used)}</strong><small>{capacity > 0 ? `/ ${format(capacity)} Token` : 'Token'}</small>
              {budgetNote && <button className="icon-button usage-budget-info" title={budgetNote} aria-label="查看可用上限说明"
                onClick={() => {if (details.current) {details.current.open = true; details.current.scrollIntoView({block: 'nearest'});}}}>
                <Info size={13} aria-hidden="true"/>
              </button>}
            </div>
            <small>{capacity > 0 ? <>占用 <b>{usedPercent.toFixed(1)}%</b></> : '预算未知'}</small>
          </div>
          {capacity > 0 && <div className="usage-capacity" role="meter" aria-label="上下文占用" aria-valuemin={0} aria-valuemax={capacity}
            aria-valuenow={Math.min(used, capacity)} aria-valuetext={`${format(used)} / ${format(capacity)} Token`}>
            <span style={{ width: Math.min(100, usedPercent) + '%' }}/>
          </div>}
          {hasComposition && <><p className="usage-composition-title">文本组成</p>
          <div className="usage-composition" aria-hidden="true">{parts.map(part =>
            <span key={part.label} className={`usage-part-${part.index}`} style={{ width: percent(part.value, total) + '%' }}/>)}</div>
          <ul className="usage-groups" aria-label="文本组成与占比">{parts.map(part =>
            <li key={part.label}>
              <span><i className={`usage-part-${part.index}`} aria-hidden="true"/>{part.label}</span>
              <span>{format(part.value)}</span>
              <small>{percent(part.value, total).toFixed(1)}%</small>
            </li>)}</ul></>}
        </> : <>
          <p className="usage-empty">{usage.context ? '暂无文本估算' : '首次请求发出后显示上下文用量'}</p>
        </>}
      </section>

      {onSave && <section className="usage-section">
        <div className="usage-heading usage-limit">
          <label htmlFor={targetId}>下次目标</label>
          <select id={targetId} value={target} disabled={busy}
            onChange={event => {
              const next = Number(event.target.value);
              setDraftTarget(next === saved ? undefined : next);
              setError('');
            }}>
            <option value={0}>未启用</option><option value={300000}>300K</option><option value={1000000}>1M</option>
          </select>
        </div>
        {target !== saved && <div className="button-row">
          <button className="secondary" disabled={busy} onClick={() => { setDraftTarget(undefined); setError(''); }}>取消</button>
          <button className="primary" disabled={busy} onClick={save}>{busy ? '正在保存…' : '保存'}</button>
        </div>}
        {error && <p className="error-text" role="alert">{error}</p>}
      </section>}

      <details className="usage-section usage-details" ref={details}>
        <summary><ChevronRight size={14} aria-hidden="true"/>统计口径与详情</summary>
        <p className="muted">生成速度根据收到的思考和正文片段估算。本轮用量合计当前或最近一轮由模型服务上报的输入与输出。上下文占用按最近一次请求估算，组成图仅计文本。</p>
        {hasMoreHistory && <p className="muted">累计用量与缓存命中率按已载入的请求统计。</p>}
        {budgetNote && <p className="muted">{budgetNote}</p>}
        <pre>{JSON.stringify({ input_tokens: usage.input, output_tokens: usage.output, last_request: usage.last ?? null, capacity: usage.capacity ?? null }, null, 2)}</pre>
      </details>
    </>}
  </section>;
}
