import type {ReviewSummary} from './reviews';
export type Attachment = { name: string; path: string; size: number };
export type ModePlugin = {id:string;mode:string;name:string;description:string;scope:'run'|'session';kind:'mode';version:string;builtin:boolean;enabled:boolean};
export type QuickAction = {id:string;plugin:string;label:string;prompt:string;configured:boolean;page:string;tab:string};
export type MediaTask = {id:string;conversation_id:string;run_id?:string;tool_call_id?:string;revision?:number;capability:string;status:string;message:string;created_at:number;updated_at:number;model:string;service_name:string;files:string[];progress?:number;phase?:'accepted'|'queued'|'generating'|'generated';can_resume:boolean;can_retry?:boolean;can_refresh?:boolean;can_redownload?:boolean;needs_configuration?:boolean;stop_requested?:boolean;diagnostic?:{stage:string;message:string;http_status?:number;code?:string;request_id?:string}};
export type Model = { connection_test?: {success:boolean;message:string;checked_at:string} | null; chat_supported?: boolean | null; purpose_label?: string; reason?: string; id: string; label: string; configured: boolean; display_name: string; model_name: string; base_url: string; channel_id: string; channel_display_name: string; provider_type: string; reasoning_efforts?: string[] };
export type ProjectRecord = {path: string; name: string; previous_paths?: string[]; pinned?: boolean; updated_at?: number; archived?: boolean; hidden?: boolean};
export type ProjectAction = 'edit' | 'open_folder' | 'archive' | 'restore';
export type Settings = { models: Model[]; selected_model_id: string; god_mode: boolean; theme: string; data_dir: string; project_workspace_root?: string; projects: ProjectRecord[]; multimedia_enabled?: boolean; media_capabilities?: string[]; plugin_actions?: QuickAction[]; context_policy: {enabled: boolean; target_tokens: number} };
export type FileReference = {path: string; url: string; name: string; kind: string; label: string; references: string[]};
export type TurnFile = {id: string; run_id: string; path: string; name: string; created_at: number; state: string; status: string; added: number | null; removed: number | null};
export type Message = { id: string; role: string; created_at?: number; content?: any; reasoning_content?: string; tool_calls?: any[]; tool_call_id?: string; result_obj?: any; meta?: Record<string, any>; file_references?: FileReference[]; turn_files?: TurnFile[]; file_history_error?: string; live?: boolean };
export type Run = { id: string; conversation_id: string; message_id: string; status: string; error: string; created_at?:number; updated_at?:number };
export type RuntimeEvent = { type: string; created_at?: number; sequence?: number; source_message_id?: string; persisted_position?: number | null; delta?: string; content?: string; data?: any; result?: any; session_id?: string; run_id?: string };
export type Conversation = { id: string; title: string; display_title?: string; has_content?: boolean; meta: Record<string, any>; updated_at: number; messages?: Message[]; render_messages?: Message[]; run?: Run | null; reviews?: ReviewSummary[]; media_tasks?: MediaTask[]; events?: any[]; draft?: { text: string; attachments: Attachment[] }; draft_revision?: number; view_revision?: number; view?: Record<string, any>; has_messages?: boolean; before?: number | null; has_more?: boolean; total?: number; ledger_start?: number; message_positions?: Record<string, number> };
export type SidebarEntry = { kind: 'chat' | 'project'; id: string };
export type SidebarGroup = { id: string; name: string };
export type SidebarLayout = {
  groups: SidebarGroup[];
  chats: Record<string, string>;
  projects: Record<string, { path: string; group_id?: string; pinned_at?: number }>;
};
export type SidebarSnapshot = { value: SidebarLayout; revision: number };
export type SidebarMethod = 'sidebar.create_group' | 'sidebar.move' | 'sidebar.pin_project';
export type WorkspaceChange = {
  path: string; absolute_path: string;
  status: 'added' | 'modified' | 'deleted' | 'submodule' | 'untracked_after_delete';
  added: number | null; removed: number | null;
};
export type WorkspaceChangeList = { repository: boolean; files: WorkspaceChange[] };
export type WorkspaceChangeRead = { directory: string; data?: WorkspaceChangeList; pending?: boolean; error?: string };
export type FileSession = { id: string; path: string; name: string; fingerprint: string; payload: any; conflict?: boolean; unavailable?: boolean };
export type Extension = {id:string; kind:'workbench'|'page'|'panel'|'editor'|'command'|'menu'|'toolbar'; target:string; entry?:string; mode?:'replace'|'append'; command?:string; title?:string; action?:'usage'; showRate?:boolean};
export type Plugin = { id: string; name: string; version: string; description: string; capabilities: string[]; hash: string; builtin?: boolean; previous?: boolean; html?: string; url?: string; token?: string; hostApi:1|2; stateVersion?:number; entry?:string; contributes:Extension[]; enabled?:boolean };
export type HostEvent = { event: string; data?: any; conversation_id?: string; run_id?: string; run?:Run; error?: string };
declare global {
  interface Window { cowork: { pathForFile: (file: File) => string; call: <T = any>(method: string, params?: any) => Promise<T>; onEvents: (callback: (events: HostEvent[]) => void) => () => void } }
}
