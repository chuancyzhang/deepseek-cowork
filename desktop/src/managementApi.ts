import type { HostEvent } from './types';

export const rpc = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
export async function operation(method: string, params?: any, progress?: (job: any) => void, signal?: AbortSignal) {
  signal?.throwIfAborted();
  const receipt = await rpc(method, params);
  if (!receipt?.job_id) { signal?.throwIfAborted(); return receipt; }
  return new Promise<any>((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout>;
    let closed = false;
    const cleanup = () => {closed = true; clearTimeout(timer); unsubscribe(); signal?.removeEventListener('abort', abort);};
    const abort = () => {
      if (closed) return;
      cleanup();
      void rpc('jobs.cancel', {id: receipt.job_id}).catch(() => undefined);
      reject(new DOMException('已取消读取资料。', 'AbortError'));
    };
    const finish = (job: any) => {
      if (closed || job.id !== receipt.job_id) return;
      progress?.(job);
      if (['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
        cleanup();
        if (job.status === 'completed') resolve(method === 'library.read' && job.result?.kind === 'library_preview'
          ? rpc('library.read_result', {source: params.source, job_id: job.id}) : job.result);
        else reject(new Error(job.error || job.message || '操作未完成，已有内容已保留。'));
      }
    };
    const unsubscribe = window.cowork.onEvents((events: HostEvent[]) => { for (const event of events) if (event.event === 'job') finish(event.data); });
    signal?.addEventListener('abort', abort, {once:true});
    if (signal?.aborted) {abort(); return;}
    const poll = async () => {
      try { finish(await rpc('jobs.get', { id: receipt.job_id })); }
      catch (failure) { if (!closed) { cleanup(); reject(failure); } }
      if (!closed) timer = setTimeout(poll, 2000);
    };
    void poll();
  });
}
