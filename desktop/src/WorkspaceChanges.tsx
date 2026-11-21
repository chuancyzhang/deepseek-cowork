import {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {ChevronDown, ChevronRight, Copy, FileDiff, FilePlus2, Folder, RefreshCw} from 'lucide-react';
import type {WorkspaceChange, WorkspaceChangeList, WorkspaceChangeRead} from './types';
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

export function ChangeSummary({files, open}: {files: WorkspaceChange[]; open: (path?: string) => void}) {
  const [expanded, setExpanded] = useState(false);
  return <section className="chat-change-summary" aria-label="工作目录变更">
    <header>
      <div><strong>工作目录变更</strong><ChangeTotals files={files}/></div>
      <button className="secondary" onClick={() => open()}><FileDiff size={15} aria-hidden="true"/>查看变更</button>
    </header>
    <div className="chat-change-files">
      {(expanded ? files : files.slice(0, 3)).map(file => <button className="chat-change-file" key={file.path}
        aria-label={`查看 ${file.path} 的变更`} title={file.path} onClick={() => open(file.path)}>
        <FilePath file={file}/><ChangeCounts added={file.added} removed={file.removed}/>
      </button>)}
      {files.length > 3 && <button className="chat-change-more" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        {expanded ? '收起文件列表' : `再显示 ${number.format(files.length - 3)} 个文件`}
        <ChevronDown size={14} aria-hidden="true" className={expanded ? 'rotated' : undefined}/>
      </button>}
    </div>
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
      result => {if (alive) setPatch(result);}, error => {if (alive) setFailure(error.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, ''));},
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
