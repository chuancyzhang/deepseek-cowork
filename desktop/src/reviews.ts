import type {Run} from './types';

export type ReviewSummary = {id: string; kind: 'office' | 'skill'; title: string; format: string; status: string; revision: number; source_message_id?: string; run_id?: string};
export type ReviewEdit = {revision: number; patch: Record<string, any>; officeText?: string; filePath?: string; fingerprint?: string};

/** Tool receipts are navigation hints, never conversation authority. */
export function reviewId(result: any): string | undefined {
  if (typeof result === 'string') {try {result = JSON.parse(result);} catch {return;}}
  return typeof result?.review?.id === 'string' ? result.review.id : undefined;
}

export function reviewTitle(item: ReviewSummary) {
  return item.kind === 'skill' ? item.title + ' · Skill' : item.title.toLowerCase().endsWith('.' + item.format) ? item.title : item.title + '.' + item.format;
}

/** Both snapshots and events carry the draft's durable revision. */
export function mergeReviews(previous: ReviewSummary[], incoming: ReviewSummary[]) {
  const entries = new Map(previous.map(item => [item.id, item]));
  let changed = false;
  for (const item of incoming) {
    const existing = entries.get(item.id);
    if (!existing || item.revision > existing.revision) {entries.set(item.id, item); changed = true;}
  }
  return changed ? [...entries.values()] : previous;
}

export function activeReview(reviews: ReviewSummary[], run: Run | null | undefined, cid: string) {
  if (!run || run.conversation_id !== cid || !['queued', 'running', 'stopping'].includes(run.status)) return;
  return reviews.find(item => item.run_id === run.id && ['generating', 'ready', 'saved'].includes(item.status));
}
