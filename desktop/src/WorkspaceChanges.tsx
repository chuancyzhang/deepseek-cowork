import {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {ChevronDown, ChevronRight, Copy, ExternalLink, FileText, FileDiff, FilePlus2, Folder, RefreshCw} from 'lucide-react';
import type {FileReference, TurnFile, WorkspaceChange, WorkspaceChangeList, WorkspaceChangeRead} from './types';
import {historyReasons, turnFileRows} from './turnFiles';
import {rpc} from './managementApi';
import MoreActions, {type MenuAction} from './MoreActions';

const number = new Intl.NumberFormat('zh-CN');
const rowLabels = {added: '新增', modified: '已修改', deleted: '已删除', submodule: '子模块', untracked_after_delete: '已取消跟踪'};

function ChangeCounts({added, removed}: {added: number | null; removed: number | null}) {
  return <span className="change-counts">
    {added != null && added > 0 && <span className="change-added" title="新增行数">+{number.format(added)}</span>}
    {removed != null && removed > 0 && <span className="change-removed" title="删除行数">−{number.format(removed)}</span>}
  </span>;
}

function FilePath({file}: {file: WorkspaceChange}) {
  const slash = file.path.lastIndexOf('/') + 1;
  return <span className="change-path" title={file.path}>{slash > 0 && <span>{file.path.slice(0, slash)}</span>}{file.path.slice(slash)}</span>;
}

function ChangeTotals({files}: {files: WorkspaceChange[]}) {
  const {added, removed} = useMemo(() => files.reduce((sum, file) => ({
    added: sum.added + (file.added ?? 0), removed: sum.removed + (file.removed ?? 0),
  }), {added: 0, removed: 0}), [files]);
  return <span className="change-totals"><span>{number.format(files.length)} 个文件</span><ChangeCounts added={added} removed={removed}/></span>;
}

export function FileSummary({references, records = [], openFile, openHistory, failure, retry}: {
  references: FileReference[]; records?: TurnFile[]; openFile: (path: string) => void; openHistory?: (files: TurnFile[], selected?: string) => void; failure?: string; retry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const files = turnFileRows(references, records);
  const added = records.reduce((sum, file) => sum + (file.added || 0), 0), removed = records.reduce((sum, file) => sum + (file.removed || 0), 0);
  if (!files.length && !failure) return null;
  return <section className="chat-change-summary" aria-label="本轮文件">
    {files.length > 0 && <header>
      <div><strong>{number.format(files.length)} 个文件</strong><ChangeCounts added={added} removed={removed}/></div>
      {records.length > 0 && <button className="secondary" onClick={() => openHistory?.(records)}>查看变更</button>}
    </header>}
    {failure && <div className="inline-error" role="alert"><p>{failure}</p>{retry && <button onClick={retry}>重试</button>}</div>}
    <div className="chat-change-files">
      {(expanded ? files : files.slice(0, 3)).map(file => <button className="chat-change-file" key={file.path}
        aria-label={file.record ? `查看本轮 ${file.name} 的记录` : `打开 ${file.name} 的当前内容`} title={file.path}
        onClick={() => file.record ? openHistory?.(records, file.record.id) : openFile(file.path)}>
        {file.record ? <FileDiff size={16} aria-hidden="true"/> : <FileText size={16} aria-hidden="true"/>}
        <span className="turn-file-name"><span>{file.name}</span>{file.record?.state === 'purged' && <small>历史正文已清理</small>}{file.record?.state === 'baseline_missing' && <small>缺少执行前内容</small>}</span>
        <ChangeCounts added={file.record?.added ?? null} removed={file.record?.removed ?? null}/>
        {file.record ? <ChevronRight size={15} aria-hidden="true"/> : <ExternalLink size={15} aria-hidden="true"/>}
      </button>)}
      {files.length > 3 && <button className="chat-change-more" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {expanded ? '收起文件列表' : `再显示 ${number.format(files.length - 3)} 个文件`}
        <ChevronDown size={14} aria-hidden="true" className={expanded ? 'rotated' : undefined}/>
      </button>}
    </div>
  </section>;
}

type HistoryDetail = TurnFile & {patch: string; text: string | null; binary?: string};
export function FileHistoryPanel({cid, files, selected, select, openFile}: {
  cid: string; files: TurnFile[]; selected: string; select: (id: string) => void; openFile: (path: string) => void;
}) {
  const [response, setResponse] = useState<{cid: string; id: string; detail?: HistoryDetail; failure?: string} | null>(null), [revision, retry] = useState(0);
  const current = response?.cid === cid && response.id === selected ? response : null;
  const detail = current?.detail, failure = current?.failure;
  useEffect(() => {
    let alive = true;
    setResponse(null);
    if (selected) void rpc<HistoryDetail>('workspace.file_history', {conversation_id: cid, id: selected}).then(
      value => {if (alive) setResponse({cid, id: selected, detail: value});}, error => {if (alive) setResponse({cid, id: selected, failure: error.message});},
    );
    return () => {alive = false;};
  }, [cid, selected, revision]);
  return <section className="workspace-changes" aria-label="本轮文件记录">
    <header className="file-toolbar"><span>{files.length} 个文件 · 只读</span><time>{files[0] && new Date(files[0].created_at * 1000).toLocaleString('zh-CN')}</time></header>
    <div className="workspace-change-list">{files.map(file => <div className="workspace-change" key={file.id}>
      <div className="workspace-change-row"><button className="workspace-change-path" aria-expanded={selected === file.id} onClick={() => select(selected === file.id ? '' : file.id)}>
        {selected === file.id ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}<span className="change-path" title={file.path}>{file.name}</span>
      </button><ChangeCounts added={file.added} removed={file.removed}/></div>
      {selected === file.id && <div className="turn-file-detail">
        <div className="file-toolbar"><span>{file.status === 'deleted' ? '本轮删除' : file.status === 'added' ? '本轮新增' : '本轮修改'}</span><button className="secondary" onClick={() => openFile(file.path)}>打开当前文件</button></div>
        {failure ? <div className="inline-error" role="alert"><p>历史记录读取失败：{failure}</p><button onClick={() => retry(revision + 1)}>重试</button></div> : !detail ? <p className="workspace-diff-empty" role="status">正在读取记录…</p> : <>
          {historyReasons[detail.state] && <p className="workspace-diff-empty" role="status">{historyReasons[detail.state]}</p>}
          {detail.patch ? <DiffCode patch={detail.patch} name={file.name}/> : detail.text != null ? <pre className="turn-file-content" tabIndex={0}>{detail.text || '（空文件）'}</pre> : null}
          {detail.binary && <a className="turn-file-download" download={file.name.split(/[\\/]/).pop()} href={'data:application/octet-stream;base64,' + detail.binary}>下载本轮副本</a>}
        </>}
      </div>}
    </div>)}</div>
  </section>;
}

export function ChangesPanel({cid, read, selection, select, openFile, refresh, error}: {
  cid: string; read: WorkspaceChangeRead | null; selection: {path: string} | null;
  select: (path: string) => void; openFile: (path: string) => void; refresh: () => Promise<void>; error: (message: string) => void;
}) {
  const selected = useRef<HTMLButtonElement>(null);
  const data = read?.data;
  const path = selection?.path || '';
  const hasSelection = data?.files.some(file => file.path === path);
  useLayoutEffect(() => {selected.current?.scrollIntoView({block: 'nearest'});}, [selection, hasSelection]);
  return <section className="workspace-changes" aria-label="工作目录变更详情">
    <header className="file-toolbar">
      {data?.repository ? <ChangeTotals files={data.files}/> : <span>工作目录变更</span>}
      <button className="icon-button" title="刷新变更" aria-label="刷新变更" disabled={read?.pending} onClick={() => void refresh()}>
        <RefreshCw size={15} className={read?.pending ? 'spin' : undefined} aria-hidden="true"/>
      </button>
    </header>
    {read?.error && <div className="inline-error" role="alert"><p>{read.error}</p><button onClick={() => void refresh()}>重试</button></div>}
    {!data && !read?.error && <div className="empty-workspace" role="status">正在读取变更…</div>}
    {data && !data.repository && <div className="empty-workspace">当前工作目录不是 Git 仓库。</div>}
    {data?.repository && !data.files.length && <div className="empty-workspace">工作目录没有变更。</div>}
    {data && data.files.length > 0 && <div className="workspace-change-list">
      {data.files.map(file => {
        const expanded = path === file.path;
        const canOpen = file.status !== 'deleted' && file.status !== 'submodule';
        return <div className="workspace-change" key={file.path}>
          <div className="workspace-change-row">
            <button className="workspace-change-path" ref={expanded ? selected : undefined} title={file.path}
              aria-expanded={expanded} onClick={() => select(expanded ? '' : file.path)}>
              {expanded ? <ChevronDown size={14} aria-hidden="true"/> : <ChevronRight size={14} aria-hidden="true"/>}
              <FilePath file={file}/>
              <span className="sr-only">{rowLabels[file.status]}</span>
            </button>
            {file.status !== 'modified' && <small>{rowLabels[file.status]}</small>}
            <ChangeCounts added={file.added} removed={file.removed}/>
            {canOpen && <button className="icon-button" title="在标签页打开" aria-label={`在标签页打开 ${file.path}`} onClick={() => openFile(file.absolute_path)}><FilePlus2 size={15} aria-hidden="true"/></button>}
            <MoreActions label="更多变更文件操作" error={error} actions={[
              ...(canOpen ? [['在资源管理器中打开', Folder, () => rpc('file.reveal', {conversation_id: cid, path: file.absolute_path})]] as MenuAction[] : []),
              ['复制路径', Copy, () => rpc('clipboard.write', {text: file.absolute_path})],
            ]}/>
          </div>
          {expanded && <WorkspaceDiff cid={cid} file={file} snapshot={data} refresh={refresh}/>}
        </div>;
      })}
    </div>}
  </section>;
}

type DiffLine = {kind: 'range' | 'added' | 'removed' | 'context' | 'note'; text: string; before?: number; after?: number};
function diffLines(patch: string): DiffLine[] {
  const lines: DiffLine[] = [];
  let before = 0, after = 0;
  for (const line of patch.split('\n')) {
    const range = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (range) {
      before = Number(range[1]); after = Number(range[2]); lines.push({kind: 'range', text: line});
    } else if (lines.length) {
      const text = line.slice(1).replace(/\r$/, '');
      switch (line[0]) {
        case '+': lines.push({kind: 'added', after: after++, text}); break;
        case '-': lines.push({kind: 'removed', before: before++, text}); break;
        case ' ': lines.push({kind: 'context', before: before++, after: after++, text}); break;
        case '\\': lines.push({kind: 'note', text: '文件末尾没有换行'}); break;
      }
    }
  }
  return lines;
}

export function DiffCode({patch, name}: {patch: string; name: string}) {
  const lines = useMemo(() => diffLines(patch), [patch]);
  return <div className="workspace-diff-scroll" tabIndex={0} aria-label={name + ' 的文本差异'}><div className="workspace-diff-code">{lines.map(({kind, text, before, after}, index) => <div key={index} className={'diff-line diff-' + kind}>
    {kind === 'range' || kind === 'note' ? <span>{text}</span> : <><span className="diff-line-number">{before}</span><span className="diff-line-number">{after}</span><span className="diff-marker">{kind === 'added' ? '+' : kind === 'removed' ? '−' : ' '}</span><code>{text || ' '}</code></>}
  </div>)}</div></div>;
}

type Patch = {patch: string; too_large?: boolean};
export function WorkspaceDiff({cid, file, snapshot, refresh}: {
  cid: string; file: WorkspaceChange; snapshot: WorkspaceChangeList; refresh: () => Promise<void>;
}) {
  const [patch, setPatch] = useState<Patch | null>(null);
  const [failure, setFailure] = useState('');
  useEffect(() => {
    let alive = true;
    setFailure('');
    void rpc<Patch>('workspace.diff', {conversation_id: cid, path: file.absolute_path}).then(
      result => {if (alive) setPatch(result);}, error => {if (alive) setFailure(error.message);},
    );
    return () => {alive = false;};
  }, [cid, file.absolute_path, snapshot]);
  const lines = useMemo(() => diffLines(patch?.patch || ''), [patch]);
  return <div className="workspace-diff">
    {failure && <div className="inline-error" role="alert"><p>{failure}</p><button onClick={() => void refresh()}>重试</button></div>}
    {lines.length > 0 ? <div className="workspace-diff-scroll" tabIndex={0} aria-label={file.path + ' 的文本差异'}>
      <div className="workspace-diff-code">{lines.map(({kind, text, before, after}, index) => <div key={index} className={'diff-line diff-' + kind}>
        {kind === 'range' || kind === 'note' ? <span>{text}</span> : <>
          <span className="diff-line-number">{before}</span><span className="diff-line-number">{after}</span>
          <span className="diff-marker">{kind === 'added' ? '+' : kind === 'removed' ? '−' : ' '}</span>
          <code>{text || ' '}</code>
        </>}
      </div>)}</div>
    </div> : !failure && <p className="workspace-diff-empty" role="status">{patch ? patch.too_large ? '变更较大，无法展开文本差异。' : '此文件没有可预览的文本差异。' : '正在读取差异…'}</p>}
  </div>;
}
