import type { Attachment } from './types';

export type Draft = { text: string; attachments: Attachment[] };
type Entry = { value: Draft; initialized: boolean; pending?: any };
type Journal = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const empty = (): Draft => ({ text: '', attachments: [] });
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const remaining = (value: Draft, submitted: Draft): Draft => ({
  text: value.text === submitted.text ? '' : value.text,
  attachments: value.attachments.filter(file => !submitted.attachments.some(sent => sent.path === file.path)),
});

/** Unsent input belongs to this editor. Only submitted messages use backend receipts. */
export class Drafts {
  listeners = new Map<string, Set<() => void>>();
  allListeners = new Set<() => void>();
  versions = new Map<string, number>();
  entries = new Map<string, Entry>();
  queues = new Map<string, Promise<any>>();
  private submitting = new Set<string>();
  rpc: (method: string, params: any) => Promise<any>;
  journal: Journal;
  constructor(rpc: (method: string, params: any) => Promise<any>, journal: Journal) { this.rpc = rpc; this.journal = journal; }
  key(id: string) { return 'cowork-pending-draft:' + id; }
  recovery(id: string): Entry | undefined {
    if (this.entries.has(id)) return this.entries.get(id);
    const raw = this.journal.getItem(this.key(id));
    return raw ? JSON.parse(raw) : undefined;
  }
  subscribe(id: string, listener: () => void) {
    if (!this.listeners.has(id)) this.listeners.set(id, new Set());
    this.listeners.get(id)!.add(listener); return () => { this.listeners.get(id)?.delete(listener); };
  }
  subscribeAll(listener: () => void) { this.allListeners.add(listener); return () => { this.allListeners.delete(listener); }; }
  get(id: string): Entry {
    if (!this.entries.has(id)) {
      const raw = this.journal.getItem(this.key(id));
      const cached = raw ? JSON.parse(raw) : undefined;
      // Old recovery records remain readable; revision/conflict flags are obsolete.
      this.entries.set(id, { value: cached?.value || empty(), pending: cached?.pending, initialized: Boolean(cached) });
    }
    return this.entries.get(id)!;
  }
  restore(id: string, legacy: { value: Draft; revision?: number }): Entry {
    const entry = this.get(id);
    // Import an older release's backend draft once. A later snapshot never
    // replaces current input, including an explicitly cleared input box.
    if (!entry.initialized) { entry.value = clone(legacy.value || empty()); entry.initialized = true; }
    this.persist(id);
    return entry;
  }
  persist(id: string) {
    const entry = this.get(id);
    try {
      if (entry.initialized || entry.pending) this.journal.setItem(this.key(id), JSON.stringify(entry));
    } catch (failure) {
      console.error('composer_recovery_failed', { conversation_id: id });
      throw failure;
    } finally {
      this.versions.set(id, (this.versions.get(id) || 0) + 1);
      for (const listener of this.listeners.get(id) || []) listener();
      for (const listener of this.allListeners) listener();
    }
  }
  change(id: string, value: Draft) {
    Object.assign(this.get(id), { value: clone(value), initialized: true });
    this.persist(id);
  }
  /** New chat carries only unsent input; an uncertain submitted message stays put. */
  async carry(from: string, to: string, prepare: (value: Draft) => Promise<Draft> = async value => value) {
    const entry = this.get(from);
    const value = clone(entry.pending?.submitted ? remaining(entry.value, entry.pending.submitted) : entry.value);
    this.change(to, await prepare(value)); // Persist the destination before releasing the source.
    this.change(from, remaining(this.get(from).value, value));
  }
  async save(id: string) { this.persist(id); }
  serial<T>(id: string, operation: () => Promise<T>): Promise<T> {
    const previous = this.queues.get(id) || Promise.resolve();
    const task = previous.catch(() => undefined).then(operation);
    this.queues.set(id, task);
    return task;
  }
  async submit(id: string, params: any) {
    return this.serial(id, async () => {
      this.submitting.add(id);
      try {
        const entry = this.get(id);
        const signature = JSON.stringify(params);
        let pending = entry.pending;
        if (pending && pending.signature !== signature) {
          throw new Error('上一条消息的接收结果尚未确认。请先核对发送状态；新输入已保留。');
        }
        if (!pending) {
          const submitted = params.consume_draft === false ? null : { text: params.text, attachments: params.attachments || [] };
          pending = { signature, submitted, params: { ...params, conversation_id: id,
            request_id: crypto.randomUUID(), message_id: crypto.randomUUID() } };
          entry.pending = pending;
          try { this.persist(id); }
          catch (failure) { entry.pending = undefined; throw failure; } // No request was sent.
        }
        let receipt;
        try { receipt = await this.rpc('conversation.send', pending.params); }
        catch (failure) {
          // A validation rejection is definitive. Transport loss remains pending
          // until a durable receipt proves what happened to this exact command.
          const state = await this.rpc('conversation.receipt', { conversation_id: id, request_id: pending.params.request_id }).catch(() => null);
          if (state?.accepted) receipt = state;
          else {
            if (state?.rejected) { entry.pending = undefined; this.persist(id); }
            throw failure;
          }
        }
        if (!receipt.accepted) throw new Error('消息尚未被接收，输入内容已保留。');
        this.acknowledge(id, pending.submitted);
        return receipt;
      } finally {this.submitting.delete(id);this.persist(id);}
    });
  }
  needsRecovery(id: string) { return Boolean(this.get(id).pending) && !this.submitting.has(id); }
  acknowledge(id: string, submitted: Draft | null) {
    const entry = this.get(id);
    if (submitted) entry.value = remaining(entry.value, submitted);
    entry.pending = undefined; entry.initialized = true;
    this.persist(id);
    return entry.value;
  }
  async recoverSubmission(id: string) {
    return this.serial(id, async () => {
      const pending = this.get(id).pending;
      if (!pending) return null;
      const receipt = await this.rpc('conversation.receipt', { conversation_id: id, request_id: pending.params.request_id });
      if (receipt.accepted) this.acknowledge(id, pending.submitted);
      else if (receipt.rejected) { this.get(id).pending = undefined; this.persist(id); }
      return receipt;
    });
  }
}
