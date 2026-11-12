import type { Attachment } from './types';

export type Draft = { text: string; attachments: Attachment[] };
type Remote = { value: Draft; revision: number };
type Entry = Remote & { dirty: boolean; conflict: boolean; generation: number; pending?: any };
type Journal = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const empty = (): Draft => ({ text: '', attachments: [] });
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

/** SQLite is authoritative; the browser journal contains only unsaved work. */
export class Drafts {
  listeners = new Map<string,Set<()=>void>>();
  allListeners = new Set<()=>void>();
  versions = new Map<string,number>();
  entries = new Map<string, Entry>();
  queues = new Map<string, Promise<any>>();
  rpc: (method: string, params: any) => Promise<any>;
  journal: Journal;
  constructor(rpc: (method: string, params: any) => Promise<any>, journal: Journal) { this.rpc = rpc; this.journal = journal; }
  key(id: string) { return 'cowork-pending-draft:' + id; }
  subscribe(id:string, listener:()=>void) {
    if (!this.listeners.has(id)) this.listeners.set(id,new Set());
    this.listeners.get(id)!.add(listener); return ()=>{this.listeners.get(id)?.delete(listener);};
  }
  subscribeAll(listener:()=>void) {this.allListeners.add(listener);return ()=>{this.allListeners.delete(listener);};}
  get(id: string): Entry {
    if (!this.entries.has(id)) this.entries.set(id, { value: empty(), revision: -1, dirty: false, conflict: false, generation: 0 });
    return this.entries.get(id)!;
  }
  restore(id: string, remote: Remote): Entry {
    const raw = this.journal.getItem(this.key(id));
    const cached = raw ? JSON.parse(raw) as Entry : undefined;
    const dirty = Boolean(cached?.dirty && !equal(cached.value, remote.value));
    const entry: Entry = { ...remote, value: clone(dirty && cached ? cached.value : remote.value || empty()),
      dirty, conflict: dirty && cached?.revision !== remote.revision, generation: 0, pending: cached?.pending };
    this.entries.set(id, entry);
    this.persist(id);
    return entry;
  }
  persist(id: string) {
    const entry = this.get(id);
    if (entry.dirty || entry.pending) this.journal.setItem(this.key(id), JSON.stringify(entry));
    else this.journal.removeItem(this.key(id));
    this.versions.set(id,(this.versions.get(id)||0)+1);
    for (const listener of this.listeners.get(id)||[]) listener();
    for (const listener of this.allListeners) listener();
  }
  change(id: string, value: Draft) {
    const entry = this.get(id);
    entry.value = clone(value); entry.dirty = true; entry.generation++;
    this.persist(id);
  }
  resolve(id: string, keepLocal: boolean, remote: Remote) {
    const entry = this.get(id);
    entry.revision = remote.revision; entry.conflict = false;
    if (!keepLocal) entry.value = clone(remote.value);
    entry.dirty = keepLocal && !equal(entry.value, remote.value);
    entry.generation++; this.persist(id);
    return entry.value;
  }
  serial<T>(id: string, operation: () => Promise<T>): Promise<T> {
    const previous = this.queues.get(id) || Promise.resolve();
    // A failed operation remains dirty and visible; it does not poison later
    // explicit recovery attempts in the queue.
    const task = previous.catch(() => undefined).then(operation);
    this.queues.set(id, task);
    return task;
  }
  async flush(id: string) {
    const entry = this.get(id);
    if (!entry.dirty) return;
    if (entry.conflict) throw new Error('草稿与已保存版本不同。当前输入已保留，请先选择保留哪一份。');
    const generation = entry.generation;
    const value = clone(entry.value);
    const receipt = await this.rpc('draft.save', { conversation_id: id, value, revision: entry.revision });
    entry.revision = receipt.revision;
    if (generation === entry.generation) entry.dirty = false;
    this.persist(id);
  }
  save(id: string) { return this.serial(id, () => this.flush(id)); }
  async submit(id: string, params: any) {
    return this.serial(id, async () => {
      const entry = this.get(id);
      const signature = JSON.stringify(params);
      let pending = entry.pending;
      if (pending && pending.signature !== signature) {
        throw new Error('上一条消息的接收结果尚未确认。请先核对发送状态；新输入已保留。');
      }
      if (!pending) {
        await this.flush(id);
        const submitted = params.consume_draft === false ? null : { text: params.text, attachments: params.attachments || [] };
        pending = { signature, submitted, params: { ...params, conversation_id: id,
          request_id: crypto.randomUUID(), message_id: crypto.randomUUID(),
          ...(submitted && equal(entry.value, submitted) ? { draft_revision: entry.revision } : {}) } };
        entry.pending = pending; this.persist(id);
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
      this.acknowledge(id, receipt.draft, pending.submitted);
      return receipt;
    });
  }
  acknowledge(id: string, remote: Remote, submitted: Draft | null) {
    const entry = this.get(id);
    const value = submitted ? { text: entry.value.text === submitted.text ? '' : entry.value.text,
      attachments: entry.value.attachments.filter(file => !submitted.attachments.some(sent => sent.path === file.path)) } : entry.value;
    Object.assign(entry, { value, revision: remote.revision, pending: undefined, conflict: false,
      dirty: !equal(value, remote.value), generation: entry.generation + 1 });
    this.persist(id);
    return value;
  }
  async recoverSubmission(id: string) {
    return this.serial(id, async () => {
      const pending = this.get(id).pending;
      if (!pending) return null;
      const receipt = await this.rpc('conversation.receipt', { conversation_id: id, request_id: pending.params.request_id });
      if (receipt.accepted) this.acknowledge(id, receipt.draft, pending.submitted);
      else if (receipt.rejected) { this.get(id).pending = undefined; this.persist(id); }
      return receipt;
    });
  }
}
