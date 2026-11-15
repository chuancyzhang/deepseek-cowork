export type ReviewSummary = {id: string; kind: 'office' | 'skill'; title: string; format: string; status: string; revision: number; source_message_id?: string};
export type ReviewEdit = {revision: number; patch: Record<string, any>; officeText?: string; filePath?: string; fingerprint?: string};

/** Tool receipts are navigation hints, never conversation authority. */
export function reviewId(result: any): string | undefined {
  if (typeof result === 'string') {try {result = JSON.parse(result);} catch {return;}}
  return typeof result?.review?.id === 'string' ? result.review.id : undefined;
}

export function reviewTitle(item: ReviewSummary) {
  return item.kind === 'skill' ? item.title + ' · Skill' : item.title.toLowerCase().endsWith('.' + item.format) ? item.title : item.title + '.' + item.format;
}
