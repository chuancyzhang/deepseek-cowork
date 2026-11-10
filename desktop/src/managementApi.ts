import type { HostEvent } from './types';

export const rpc = <T = any,>(method: string, params?: any) => window.cowork.call<T>(method, params);
export async function operation(method: string, params?: any, progress?: (job: any) => void) {
  const receipt = await rpc(method, params);
  if (!receipt?.job_id) return receipt;
  return new Promise<any>((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout>;
    let closed = false;
    const finish = (job: any) => {
      if (closed || job.id !== receipt.job_id) return;
      progress?.(job);
      if (['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
        closed = true; clearTimeout(timer); unsubscribe();
        if (job.status === 'completed') resolve(job.result);
        else reject(new Error(job.error || job.message || '操作未完成，已有内容已保留。'));
      }
    };
    const unsubscribe = window.cowork.onEvents((events: HostEvent[]) => { for (const event of events) if (event.event === 'job') finish(event.data); });
    const poll = async () => {
      try { finish(await rpc('jobs.get', { id: receipt.job_id })); }
      catch (failure) { if (!closed) { closed = true; unsubscribe(); reject(failure); } }
      if (!closed) timer = setTimeout(poll, 2000);
    };
    void poll();
  });
}
