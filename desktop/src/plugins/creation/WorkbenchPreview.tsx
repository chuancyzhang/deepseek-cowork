import {useEffect, useRef, useState, type KeyboardEvent, type MouseEvent} from 'react';
import {ArrowUp, ChartColumn, Folder, Puzzle, Square} from 'lucide-react';
import Sidebar from '../../Sidebar';
import Usage, {UsageToolbarButton} from '../../Usage';
import {StoreContext} from '../../host/context';
import {HostStore} from '../../host/store';
import {previewSnapshot} from '../../host/sdk';
import {themeStyle} from '../../theme';
import type {Conversation, Extension, Plugin, RuntimeEvent} from '../../types';
import PluginFrame from '../PluginFrame';
import {MoreActions} from '../BuiltinWorkbench';
import './creation.css';

export const previewPages = [['对话','对话'], ['首页布局','首页'], ['工作区','工作区'], ['管理页面','管理页面']] as const;
const conversation: Conversation = {...previewSnapshot('light').conversation, updated_at:0};
const sampleRun = 'preview';
const contextPolicy = {context_policy:{enabled:false,target_tokens:0}};
const chunks = ['本周已完成资料整理。','接下来核对待办，','补全负责人和时间，','最后同步更新给团队。'];

function useSampleRun() {
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  function start() {
    const now = Date.now() / 1000;
    setEvents([{type:'observability', run_id:sampleRun, created_at:now,
      data:{type:'provider_request_start',request_id:sampleRun}}]);
    setRunning(true);
  }
  useEffect(() => {
    if (!running) return;
    let count = 0;
    const timer = window.setInterval(() => {
      const delta = chunks[count % chunks.length];
      count++;
      setEvents(previous => [...previous,{type:'content', run_id:sampleRun, delta, created_at:Date.now()/1000}]);
      if (count >= 12) setRunning(false);
    }, 450);
    return () => window.clearInterval(timer);
  }, [running]);
  return {events, activeRunId:running ? sampleRun : undefined, start, stop:() => setRunning(false)};
}

export function WorkbenchPreview({theme, page = '对话', scope, picking = false, pick, toolbar, changePage}: {
  theme?: unknown; page?: string; scope?: string; picking?: boolean; pick?: (name:string) => void;
  toolbar?: Extension; changePage?: (page:string) => void;
}) {
  const [store] = useState(() => new HostStore());
  const [draft, setDraft] = useState('');
  const [question, setQuestion] = useState(conversation.messages![0].content as string);
  const [showUsage, setShowUsage] = useState(false);
  const usageButton = useRef<HTMLButtonElement>(null);
  const {events, activeRunId, start, stop} = useSampleRun();
  const text = events.filter(event => event.type === 'content').map(event => event.delta).join('');
  const inChat = page === '对话';
  const region = (name:string) => ({
    'data-creation-region':name,
    className:'creation-region' + (scope === name ? ' region-selected' : ''),
    role:picking ? 'button' : undefined,
    tabIndex:picking ? 0 : undefined,
    'aria-label':picking ? '选择' + name : undefined,
    onClickCapture:(event:MouseEvent) => { if (picking && pick) { event.stopPropagation();pick(name); } },
    onKeyDownCapture:(event:KeyboardEvent) => {
      if (picking && pick && ['Enter',' '].includes(event.key)) { event.preventDefault();event.stopPropagation();pick(name); }
    },
  });
  function toggleRun() {
    if (activeRunId) { stop();return; }
    if (draft.trim()) { setQuestion(draft.trim());setDraft(''); }
    start();
  }
  function closeUsage() { setShowUsage(false);usageButton.current?.focus(); }
  return <div className={'creation-sample app-shell' + (picking ? ' is-picking' : '')} style={themeStyle(theme)}>
    <div {...region('侧栏导航')} className={region('侧栏导航').className+' creation-sample-sidebar'}>
      <StoreContext.Provider value={store}><Sidebar conversations={[conversation]} supplemental={[]} projects={[]} layout={null}
        selected={inChat ? conversation.id : ''} route={inChat ? 'chat' : page==='管理页面' ? 'capabilities' : 'chat'}
        running={new Set(activeRunId ? [conversation.id] : [])} toolsRef={null} hasMore={false}
        onSelect={() => changePage?.('对话')} onNew={() => changePage?.('首页布局')}
        onRoute={() => changePage?.('管理页面')} onProject={() => {}} onAction={() => {}}
        onProjectAction={() => {}} onMore={async () => {}} onLayoutAction={async () => ({groups:[],chats:{},projects:{}})} onError={() => {}}/>
      </StoreContext.Provider>
    </div>
    <main className="main-shell creation-sample-main" onKeyDown={event => {
      if (event.key === 'Escape' && showUsage) { event.stopPropagation();closeUsage(); }
    }}>
      <header {...region('顶部工具栏')} className={region('顶部工具栏').className+' app-header'}>
        <div className="header-title"><span className="title-button">{inChat ? conversation.title : '我的工作台'}</span></div>
        <div className="header-actions">
          <button className="toolbar-button" onClick={() => changePage?.(page === '工作区' ? '对话' : '工作区')}><Folder size={16}/>工作区</button>
          {inChat && toolbar?.action === 'usage' && <UsageToolbarButton events={events} activeRunId={activeRunId}
            showRate={toolbar.showRate} open={showUsage} onClick={() => setShowUsage(!showUsage)} buttonRef={usageButton}/>}
          {inChat && !toolbar && <MoreActions label="更多聊天工具" triggerRef={usageButton}
            actions={[[ '用量统计', ChartColumn, async () => setShowUsage(true) ]]} error={() => {}}/>}
        </div>
      </header>
      {inChat && showUsage && <div className="header-popovers"><Usage events={events} activeRunId={activeRunId}
        settings={contextPolicy} onClose={closeUsage}/></div>}
      <section {...region(inChat ? '整个界面' : page)} className={region(inChat ? '整个界面' : page).className+' creation-sample-page'}>
        {inChat ? <div className="message-scroll"><div className="messages">
          <article className="message user"><div className="message-body"><div className="user-text">{question}</div></div></article>
          <article className="message assistant"><div className="markdown"><p>{text || '我整理了本周的进展和接下来的安排。'}</p></div></article>
        </div></div> : page==='首页布局' ? <div className="welcome"><div className="welcome-eyebrow">从一个想法，开始今天的工作</div>
          <h2>今天，想一起完成什么？</h2><p>整理资料、分析数据，或把一个想法做出来。</p></div>
          : page==='工作区' ? <div className="creation-sample-detail"><h2>工作区</h2><div className="sample-workspace-tabs">对话　文件　任务　终端</div>
            <h3>项目笔记.md</h3><p>保留想法与已完成的成果。</p></div>
          : <div className="creation-sample-detail"><h2>能力</h2><div className="sample-workspace-tabs">技能　MCP 服务　专家　插件</div>
            <div className="sample-plugin"><Puzzle size={18}/>创造模式</div></div>}
      </section>
      {(inChat || page==='首页布局') && <div {...region('对话输入区')} className={region('对话输入区').className+' composer-container'}>
        <div className="composer">
          <textarea aria-label="示例消息" value={draft} onChange={event => setDraft(event.target.value)} placeholder="输入示例消息"/>
          <div className="composer-toolbar"><span className="muted">示例对话</span><button className="send-button"
            aria-label={activeRunId ? '停止模拟' : '模拟生成'} title={activeRunId ? '停止模拟' : '模拟生成'} onClick={() => {
              if (!inChat) changePage?.('对话');
              toggleRun();
            }}>{activeRunId ? <Square size={15}/> : <ArrowUp size={17}/>}</button></div>
        </div>
      </div>}
    </main>
  </div>;
}

export function CandidateView({plugin, extension, theme, mode, page, changePage}: {
  plugin:Plugin; extension:Extension; theme?:unknown; mode:string; page?:string; changePage?:(page:string)=>void;
}) {
  if (extension.kind === 'toolbar') return <WorkbenchPreview theme={theme} page={page} changePage={changePage} toolbar={extension}/>;
  return <PluginFrame key={plugin.hash+':'+extension.id+':'+mode} plugin={plugin} extension={extension}
    mode="preview" previewTheme={mode} scope={{extensionId:extension.id}}/>;
}
