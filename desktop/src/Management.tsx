import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { ArrowLeft, BookOpen, Boxes, CircleUserRound, Clock3, KeyRound, LoaderCircle, Palette, Plug, Puzzle, Settings2, SlidersHorizontal, Sparkles, Star, Users, X, Check, MessageSquare, ShieldCheck, Folder, Wrench, Search } from 'lucide-react';
import { rpc } from './managementApi';
import type { Settings } from './types';
import {useHostField} from './host/context';
const Capabilities = lazy(() => import('./management/Capabilities'));
const Preferences = lazy(() => import('./management/Preferences'));
const Library = lazy(() => import('./management/Library'));
const SystemSettings = lazy(() => import('./management/SystemSettings'));
const Enterprise = lazy(() => import('./management/Enterprise'));
const PluginManager = lazy(() => import('./plugins/PluginManager'));

export const managementNames: Record<string, string> = { capabilities: '能力', library: '资料库', favorites: '常用任务', team: '团队空间', settings: '设置' };
const groups: Record<string, [string, string, any][]> = {
  capabilities: [['skills', '技能', Sparkles], ['mcp', 'MCP 服务', Plug], ['experts', '专家', CircleUserRound], ['plugins', '界面插件', Puzzle]],
  favorites: [['favorites', '常用任务', Star], ['schedule_history', '运行记录', Clock3]],
  settings: [['appearance', '外观与阅读', Palette], ['preferences', '回答偏好', MessageSquare], ['models', '模型与服务', Boxes],
    ['im', '消息连接', Plug], ['enterprise', '企业账号', Users], ['security', '数据安全', ShieldCheck], ['variables', '变量与凭据', KeyRound],
    ['storage', '文件与存储', Folder], ['memory', '长期记忆', BookOpen], ['experiments', '实验功能', SlidersHorizontal],
    ['components', '组件与依赖', Wrench], ['logs', '运行日志', Clock3], ['update', '更新与关于', Settings2], ['jobs', '后台操作', Clock3]],
  library: [['library', '资料库', BookOpen]], team: [['team', '团队空间', Users]],
};

export default function Management({ route, settings, updateSettings, openConversation, onReference, onError, confirm, registerLeave }: {
  route: string; settings: Settings | null; updateSettings: (value: Settings) => void; openConversation: (id: string) => void;
  onReference: (ref: any) => Promise<void>; onError: (message: string) => void;
  confirm: (title: string, detail: string, choices: string[]) => Promise<number>; registerLeave: (callback: () => Promise<boolean>) => void;
}) {
  const tabs = groups[route] || groups.settings;
  const [tab, setTab] = useHostField('management:tab:' + route, tabs[0][0]);
  const [dirty, setDirty] = useState(false), [query, setQuery] = useState('');
  const leave = useCallback(async () => !dirty || await confirm('保留未保存的编辑？', '编辑草稿会保留在本机，下次进入可以继续。只有点击保存才会修改配置。', ['继续编辑', '保留草稿并离开']) === 1, [dirty, confirm]);
  useEffect(() => { registerLeave(leave); return () => registerLeave(async () => true); }, [leave, registerLeave]);
  const common = { onError, confirm, onDirty: setDirty };
  return <div className={'management-layout ' + (route === 'capabilities' ? 'capabilities-layout' : route === 'library' ? 'library-layout' : '')}>
    {tabs.length > 1 && <nav className="management-nav" aria-label={managementNames[route]}>{route === 'settings' && <label className="management-nav-search"><Search size={14}/><input aria-label="搜索设置" placeholder="搜索设置" value={query} onChange={event => setQuery(event.target.value)}/></label>}{tabs.filter(([,label]) => label.includes(query)).map(([id, label, Icon]) => <button key={id} aria-current={tab === id ? 'page' : undefined} className={tab === id ? 'selected' : ''} onClick={async () => { if (tab !== id && await leave()) { setDirty(false); setTab(id); } }}><Icon size={17}/><span>{label}</span></button>)}</nav>}
    <div className="management-content"><Suspense fallback={<div className="loading"><LoaderCircle className="spin"/>正在打开…</div>}>
      {['skills', 'mcp', 'experts', 'favorites', 'schedule_history'].includes(tab) ? <Capabilities key={tab} kind={tab} {...common} openConversation={openConversation}/>
        : tab === 'plugins' ? <PluginManager/>
        : tab === 'library' ? <Library {...common} onReference={onReference}/>
        : ['enterprise', 'team'].includes(tab) ? <Enterprise kind={tab} {...common} openConversation={openConversation}/>
        : ['preferences','im','security','storage','memory','experiments','components','logs','update'].includes(tab) ? <SystemSettings key={tab} kind={tab} {...common}/>
        : <Preferences key={tab} kind={tab} settings={settings} updateSettings={updateSettings} {...common}/>}
    </Suspense></div>
  </div>;
}

export function PageHead({ title, detail, children, back, compact = false }: { title: string; detail?: string; children?: React.ReactNode; back?: () => void; compact?: boolean }) {
  return <header className={'page-head' + (compact ? ' page-head-compact' : '')}><div>{back && <button className="back-link" onClick={back}><ArrowLeft size={16}/>返回</button>}<h2 className={compact ? 'sr-only' : undefined}>{title}</h2>{detail && <p>{detail}</p>}</div><div className="button-row">{children}</div></header>;
}
export function PageToolbar({ title, children }: { title: string; children?: React.ReactNode }) {
  return <header className="management-toolbar"><h2 className="sr-only">{title}</h2>{children}</header>;
}
export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="inline-error" role="alert"><p>{message}</p>{retry && <button className="secondary" onClick={retry}>重试</button>}</div>;
}
export function Empty({ icon: Icon = Boxes, title, detail, children }: { icon?: any; title: string; detail: string; children?: React.ReactNode }) {
  return <div className="management-empty"><Icon size={32}/><h3>{title}</h3><p>{detail}</p>{children}</div>;
}
export function Field({ label, help, children, wide }: { label: string; help?: string; children: React.ReactNode; wide?: boolean }) {
  return <label className={wide ? 'wide' : ''}><span>{label}</span>{children}{help && <small>{help}</small>}</label>;
}
export function JobProgress({ job }: { job: any }) {
  const [code, setCode] = useState(''), [failure, setFailure] = useState('');
  return job && !['completed', 'failed', 'cancelled'].includes(job.status) ? <div className="job-progress" role="status"><LoaderCircle size={16} className="spin"/><span>{job.message || '正在处理…'}</span>{job.url && <button className="secondary" onClick={() => void rpc('external.open', { url: job.url })}>打开登录页</button>}<button className="icon-button" title="取消操作" onClick={() => void rpc('jobs.cancel', { id: job.id })}><X size={15}/></button>{job.qr_data && <img className="connection-qr" src={job.qr_data} alt="连接二维码"/>}{job.verification_required && <div className="input-action"><input aria-label="手机配对码" value={code} onChange={e => setCode(e.target.value)}/><button type="button" onClick={async () => {try {await rpc('im.verify', {code}); setCode(''); setFailure('');} catch (error) {setFailure((error as Error).message);}}}>确认配对码</button></div>}{failure && <span className="error-text">{failure}</span>}</div> : null;
}

export function useFormDraft(key: string, initial: any, onDirty: (dirty: boolean) => void, secrets: string[] = []) {
  const [entry,setEntry] = useHostField<any>('form:' + key, () => {
    const cached=localStorage.getItem('form:' + key);
    return {value:cached ? {...initial,...JSON.parse(cached)} : initial, saved:initial};
  });
  const {value,saved}=entry;
  const dirty = JSON.stringify(value) !== JSON.stringify(saved);
  useEffect(()=>{if(!dirty&&JSON.stringify(initial)!==JSON.stringify(saved))setEntry({value:initial,saved:initial});},[initial,dirty,saved]);
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  function change(next: any) {
    setEntry({...entry,value:next});
    const publicDraft = { ...next }; for (const key of secrets) delete publicDraft[key];
    localStorage.setItem('form:' + key, JSON.stringify(publicDraft));
  }
  function done(next = value) {
    let remaining = false;
    setEntry((previous:any)=>{
      const current=JSON.stringify(previous.value)===JSON.stringify(value)?next:previous.value;
      remaining=JSON.stringify(current)!==JSON.stringify(next);
      if(remaining){const cached={...current};for(const name of secrets)delete cached[name];localStorage.setItem('form:'+key,JSON.stringify(cached));}
      else localStorage.removeItem('form:'+key);
      return {value:current,saved:next};
    });onDirty(remaining);
  }
  return { value, change, done, dirty, reset: () => {setEntry({value:saved,saved});localStorage.removeItem('form:'+key);onDirty(false);} };
}
