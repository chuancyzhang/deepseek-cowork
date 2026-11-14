/** Form drafts keep their saved baseline; browsing a legacy draft is not an edit. */
type Values = Record<string, any>;
type Snapshot = { value: Values; saved: Values };
export type FormDraft = Snapshot & { version: 1; recovery?: Snapshot };

export function sameFormValue(left: any, right: any): boolean {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object' || Array.isArray(left) !== Array.isArray(right)) return false;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && sameFormValue(left[key], right[key]));
}

function rebase(initial: Values, draft: Snapshot): Values {
  const value = { ...initial };
  for (const key of new Set([...Object.keys(draft.saved), ...Object.keys(draft.value)])) {
    if (sameFormValue(draft.value[key], draft.saved[key])) continue;
    if (Object.hasOwn(draft.value, key)) value[key] = draft.value[key];
    else delete value[key];
  }
  return value;
}

export function prepareFormDraft(initial: Values, cached: any, offerLegacy = false): FormDraft {
  if (!cached) return { version: 1, value: initial, saved: initial };
  const snapshot = cached.value && cached.saved;
  const value = snapshot ? rebase(initial, cached) : { ...initial, ...cached };
  if (cached.version !== 1 && offerLegacy && !sameFormValue(value, initial)) {
    return { version: 1, value: initial, saved: initial, recovery: { value, saved: initial } };
  }
  return { version: 1, value, saved: initial, ...(cached.recovery ? { recovery: cached.recovery } : {}) };
}

export function restoreFormRecovery(entry: FormDraft): FormDraft {
  return entry.recovery ? { version: 1, value: rebase(entry.saved, entry.recovery), saved: entry.saved } : entry;
}

export function publicFormDraft(entry: FormDraft, secrets: string[]): FormDraft {
  const publicValues = (value: Values) => Object.fromEntries(Object.entries(value).filter(([key]) => !secrets.includes(key)));
  const publicSnapshot = (snapshot: Snapshot) => ({ value: publicValues(snapshot.value), saved: publicValues(snapshot.saved) });
  return { version: 1, ...publicSnapshot(entry), ...(entry.recovery ? { recovery: publicSnapshot(entry.recovery) } : {}) };
}
