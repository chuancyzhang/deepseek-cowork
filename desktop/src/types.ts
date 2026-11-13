export type Attachment = { name: string; path: string; size: number };
export type Model = { id: string; label: string; configured: boolean; display_name: string; model_name: string; base_url: string; channel_id: string; channel_display_name: string; provider_type: string; reasoning_efforts?: string[] };
export type Settings = { models: Model[]; selected_model_id: string; god_mode: boolean; theme: string; data_dir: string; projects: any[]; context_policy: {enabled: boolean; target_tokens: number} };
export type Message = { id: string; role: string; created_at?: number; content?: any; reasoning_content?: string; tool_calls?: any[]; tool_call_id?: string; result_obj?: any; meta?: Record<string, any>; live?: boolean };
export type Run = { id: string; conversation_id: string; message_id: string; status: string; error: string; created_at?:number; updated_at?:number };
export type RuntimeEvent = { type: string; created_at?: number; sequence?: number; source_message_id?: string; delta?: string; content?: string; data?: any; result?: any; session_id?: string; run_id?: string };
export type Conversation = { id: string; title: string; display_title?: string; has_content?: boolean; meta: Record<string, any>; updated_at: number; messages?: Message[]; render_messages?: Message[]; run?: Run | null; events?: any[]; draft?: { text: string; attachments: Attachment[] }; draft_revision?: number; view_revision?: number; view?: Record<string, any>; has_messages?: boolean; before?: number | null; has_more?: boolean; total?: number; ledger_start?: number; message_positions?: Record<string, number> };
export type FileSession = { id: string; path: string; name: string; fingerprint: string; payload: any; conflict?: boolean; unavailable?: boolean };
export type Extension = {id:string; kind:'workbench'|'page'|'panel'|'editor'|'command'|'menu'; target:string; entry?:string; mode?:'replace'|'append'; command?:string; title?:string};
export type Plugin = { id: string; name: string; version: string; description: string; capabilities: string[]; hash: string; builtin?: boolean; previous?: boolean; html?: string; url?: string; token?: string; hostApi:1|2; stateVersion?:number; entry:string; contributes:Extension[]; enabled?:boolean };
export type HostEvent = { event: string; data?: any; conversation_id?: string; run_id?: string; run?:Run; error?: string };
declare global {
  interface Window { cowork: { pathForFile: (file: File) => string; call: <T = any>(method: string, params?: any) => Promise<T>; onEvents: (callback: (events: HostEvent[]) => void) => () => void } }
}
