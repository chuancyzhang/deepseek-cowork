import type { RuntimeEvent } from './types';

export const SPEED_TREND_SECONDS = 20;
const SPEED_WINDOW = 3;

type ContextUsage = {
  status?: string;
  counts?: Record<string, number>;
  total?: number;
  has_media?: boolean;
};
type Capacity = { effective_tokens?: number } & Record<string, unknown>;
type RequestUsage = {
  request_id?: string;
  input_tokens?: number;
  output_tokens?: number;
  cached_input_tokens?: number;
} & Record<string, unknown>;
export type SpeedRequest = {
  id: string;
  content: number;
  thinking: number;
  contentSnapshot?: string;
  firstOutputAt?: number;
  samples: { at: number; tokens: number }[];
};

function emptyUsage() {
  return {
    input: 0, output: 0, cached: 0, cacheInput: 0, count: 0,
    context: undefined as ContextUsage | undefined,
    capacity: undefined as Capacity | undefined,
    last: undefined as RequestUsage | undefined,
  };
}

function tokenWeight(text: string) {
  // Match the previous estimate of one token per non-ASCII or four ASCII characters.
  let weight = 0;
  for (const character of text) {
    if (!/\s/.test(character)) weight += character.charCodeAt(0) < 128 ? .25 : 1;
  }
  return weight;
}

/** Project recorded usage and stream timing while the popover is open. */
export class UsageProjection {
  private previous: RuntimeEvent[] = [];
  private usage = emptyUsage();
  private counted = new Set<string>();
  private requests = new Map<string, SpeedRequest>();
  private lastSpeed?: { rate: number; at: number };

  project(events: RuntimeEvent[], activeRunId?: string) {
    // History can be replaced or prepended. Live batches keep the previous objects.
    const previous = this.previous;
    if (events.length < previous.length || previous.length &&
      (events[0] !== previous[0] || events[previous.length - 1] !== previous.at(-1))) {
      this.previous = [];
      this.usage = emptyUsage();
      this.counted.clear();
      this.requests.clear();
      this.lastSpeed = undefined;
    }
    for (let i = this.previous.length; i < events.length; i++) this.apply(events[i]);
    this.previous = events;
    return {
      ...this.usage,
      request: activeRunId ? this.requests.get(activeRunId) : undefined,
      lastSpeed: this.lastSpeed?.rate,
    };
  }

  private apply(event: RuntimeEvent) {
    const { type, data, created_at: at, run_id: runId } = event;
    if (type === 'observability') {
      if (data?.type === 'system_prompt') this.usage.context = data.context_usage;
      else if (data?.type === 'context_capacity') this.usage.capacity = data.capacity;
      else if (data?.type === 'llm_usage' && data.usage) this.addUsage(runId, data.usage);
      else if (runId && data?.request_id) this.requestBoundary(runId, data, at);
      return;
    }
    if (!runId) return;
    if (type === 'final') this.requests.delete(runId);
    const request = this.requests.get(runId);
    if (request && at != null && (type === 'thinking' || type === 'content' || type === 'content_snapshot')) {
      const before = Math.ceil(request.thinking + request.content);
      if (type === 'thinking') request.thinking += tokenWeight(event.delta || '');
      if (type === 'content') {
        request.content += tokenWeight(event.delta || '');
        request.contentSnapshot = undefined;
      }
      if (type === 'content_snapshot') {
        const content = event.content || '';
        const previous = request.contentSnapshot;
        request.content = previous != null && content.startsWith(previous)
          ? request.content + tokenWeight(content.slice(previous.length))
          : tokenWeight(content);
        request.contentSnapshot = content;
      }
      const added = Math.ceil(request.thinking + request.content) - before;
      if (added > 0) {
        request.firstOutputAt ??= at;
        const samples = request.samples;
        samples.push({ at, tokens: added });
        const cutoff = at - SPEED_TREND_SECONDS - SPEED_WINDOW;
        if (samples[0].at < cutoff - 1) request.samples = samples.filter(sample => sample.at >= cutoff);
      }
    }
  }

  private addUsage(runId: string | undefined, usage: RequestUsage) {
    const { request_id, input_tokens, output_tokens, cached_input_tokens } = usage;
    if (typeof input_tokens !== 'number' || typeof output_tokens !== 'number') return;
    if (request_id) {
      const id = `${runId}:${request_id}`;
      if (this.counted.has(id)) return;
      this.counted.add(id);
    }
    this.usage.input += input_tokens;
    this.usage.output += output_tokens;
    this.usage.count++;
    this.usage.last = usage;
    if (typeof cached_input_tokens === 'number') {
      this.usage.cached += cached_input_tokens;
      this.usage.cacheInput += input_tokens;
    }
  }

  private requestBoundary(runId: string, data: { type: string; request_id: string; status?: string }, at?: number) {
    const request = this.requests.get(runId);
    if (data.type === 'provider_request_start' || data.type === 'provider_retry' && request?.id === data.request_id) {
      this.requests.set(runId, { id: data.request_id, content: 0, thinking: 0, samples: [] });
      return;
    }
    if (!request || request.id !== data.request_id) return;
    if (data.type !== 'provider_request_error' && data.type !== 'provider_request_finish') return;
    this.requests.delete(runId);
    const tokens = Math.ceil(request.thinking + request.content);
    if (data.status === 'completed' && tokens > 0 && at != null && request.firstOutputAt != null && at > request.firstOutputAt &&
      (!this.lastSpeed || at >= this.lastSpeed.at)) {
      this.lastSpeed = { at, rate: tokens / (at - request.firstOutputAt) };
    }
  }
}

export function generationRates(request: SpeedRequest | undefined, now: number) {
  if (!request) return [];
  const first = request.firstOutputAt;
  if (first == null || now - first < 1) return [];
  let left = 0, right = 0, tokens = 0;
  const samples = request.samples;
  return Array.from({ length: SPEED_TREND_SECONDS + 1 }, (_, second) => {
    const at = now - SPEED_TREND_SECONDS + second;
    if (at - first < 1) return undefined;
    while (right < samples.length && samples[right].at <= at) tokens += samples[right++].tokens;
    while (left < right && samples[left].at < at - SPEED_WINDOW) tokens -= samples[left++].tokens;
    return tokens / Math.min(SPEED_WINDOW, at - first);
  });
}
