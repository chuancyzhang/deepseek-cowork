import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { ArchiveRestore, ArrowLeft, BookOpen, Check, ChevronDown, ChevronRight, ClipboardPaste, Clock3, Copy, Ellipsis, ExternalLink, FileText, Folder, FolderOpen, FolderPlus, HardDrive, Link2, LogOut, MapPin, Pencil, Plus, RefreshCw, Scissors, Search, Trash2, Upload, Users, X } from 'lucide-react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Empty, ErrorState, Field, JobProgress } from '../Management';
import SidebarMenu, { type MenuAnchor } from '../SidebarMenu';
import OfficeEditor from '../OfficeEditor';
import type { FileSession } from '../types';
import { operation, rpc } from '../managementApi';
import { useHostField } from '../host/context';
import { useWorkbench } from '../host/Host';
import { appendLibraryRows, libraryCollections, libraryIdentity, libraryItemId, libraryItemKey, librarySpaces,
  libraryVisitKey, loadLibraryVisits, saveLibraryVisits, type LibrarySelection, type LibraryVisit } from './libraryCatalog';

const sources = [['local', '本地资料库'], ['weknora', 'WeKnora'], ['tencent-docs', '腾讯文档'], ['lexiang', '腾讯乐享']];
const sourceIcon = (source: string) => source === 'local' ? HardDrive : source === 'tencent-docs' ? FileText : BookOpen;
const sourceLabels: Record<string, {all: string; mine?: string; groups: string; collection: string; search: string}> = {
  weknora: {all:'全部知识库', mine:'我的知识库', groups:'共享空间', collection:'引用整个知识库', search:'搜索知识库'},
  'tencent-docs': {all:'协作空间', groups:'协作空间', collection:'引用整个空间', search:'搜索腾讯文档'},
  lexiang: {all:'全部知识库', groups:'团队', collection:'引用整个知识库', search:'搜索腾讯乐享'},
};

type Props = {
  scope?: string; projectsKey: string;
  onError: (message: string) => void;
  onReference: (source: string, items: LibrarySelection[]) => Promise<void>;
  confirm: (title: string, detail: string, choices: string[]) => Promise<number>;
  registerLeave?: (callback?: () => Promise<boolean>) => void;
};
type Naming = {item?: any; initial: string; value: string; error?: string};
type RecycleBatch = {id: string; time: number; items: {name: string; relative: string; is_folder: boolean}[]};
type Menu = {anchor: MenuAnchor; items: any[]; row?: any; kind?: 'source' | 'create' | 'recent'};
type Notice = {text: string; undo?: string};
type DocumentDraft = {name: string; text: string; original: string; extension?: 'md' | 'txt'; item?: any; file?: FileSession; manual?: boolean};
type Clipboard = {mode: 'copy' | 'move'; items: {item: string; revision: string}[]};
const title = (item: any) => String(item?.title || item?.name || item?.file_name || '未命名资料');
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const publicRevisionKey = 'library:public-revision';
const emptyView = {catalog:null, collection:null, parents:[], rows:[], page:1, more:false, query:'', searchTerm:'', searching:false, loaded:false, scroll:0, unavailable:[]};
function date(value: any) {
  if (!value) return '—';
  const timestamp = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(timestamp.getTime()) ? '—' : timestamp.toLocaleDateString('zh-CN');
}
function list(value: any): any[] {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== 'object') return [];
  for (const key of ['data', 'items', 'knowledge', 'files', 'results', 'documents', 'list']) {
    const items = list(value[key]);
    if (items.length) return items;
  }
  return [];
}
function suffixStart(name: string) {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? dot : name.length;
}

export default function Library(props: Props) {
  const [source, setSource] = useHostField((props.scope || 'library') + ':source', 'local');
  return <LibrarySource key={source} {...props} source={source} setSource={setSource}/>;
}

function LibrarySource({source, setSource, onError, onReference, registerLeave, confirm, projectsKey, scope = 'library'}: Props & {source: string; setSource: (source: string) => void}) {
  const {addProject, host} = useWorkbench();
  const local = source === 'local';
  const sourceName = sources.find(item => item[0] === source)?.[1] || '';
  const labels = sourceLabels[source];
  const [status, setStatus] = useState<any>(null);
  const [recent, setRecent] = useHostField(scope + ':recent', false);
  const [visits, setVisits] = useHostField<LibraryVisit[]>('library:visits', loadLibraryVisits);
  const [recentTarget, setRecentTarget] = useHostField<{visit: LibraryVisit; location: boolean} | null>(scope + ':visit-target', null);
  const [clipboard, setClipboard] = useHostField<Clipboard | null>('library:clipboard', null);
  const [view, setView] = useHostField<any>(scope + ':view:' + source, emptyView);
  const {catalog, collection, parents, rows, page, more, query, searching} = view;
  const searchTerm = searching ? (view.searchTerm ?? query.trim()) : '';
  const category = view.category === 'mine' && !labels?.mine ? 'all' : view.category || 'all';
  const directory = !collection && !searching;
  const publicFiles = local && collection?.id === 'public';
  const [naming, setNaming] = useHostField<Naming | null>(scope + ':naming:' + source, null);
  const [selection, setSelection] = useState<Set<string>>(() => new Set());
  const [menu, setMenu] = useState<Menu | null>(null);
  const [recycle, setRecycle] = useState<RecycleBatch[] | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState(''), [job, setJob] = useState<any>(null);
  const [form, setForm] = useState({url:'', email:'', password:'', company:'', token:''}), [authUrl, setAuthUrl] = useState('');
  const [preview, setPreview] = useState<any>(null), [notice, setNotice] = useState<Notice | null>(null);
  const [editor, setEditor] = useHostField<DocumentDraft | null>(scope + ':editor:' + source, null);
  const editorFlush = useRef<(() => Promise<string>) | undefined>(undefined);
  const [reading, setReading] = useState(false), [readJob, setReadJob] = useState<any>(null);
  const readRequest = useRef<AbortController | null>(null);
  const generation = useRef(0), alive = useRef(true), pending = useRef(false);
  const anchor = useRef(''), scroller = useRef<HTMLDivElement>(null), searchInput = useRef<HTMLInputElement>(null);
  const nameInput = useRef<HTMLInputElement>(null), allInput = useRef<HTMLInputElement>(null);
  const rangePick = useRef(false);
  const newFolderButton = useRef<HTMLButtonElement>(null);
  const update = (patch: any) => setView((previous: any) => ({...previous, ...patch}));
  const dismissMenu = useCallback(() => setMenu(null), []);
  const connected = local || Boolean(status?.sources[source] && !status.sources[source].unavailable);
  const spaces = librarySpaces(catalog);
  const spaceList = directory && category === 'spaces';
  const catalogSearch = directory && (local || source === 'weknora' || spaceList);
  const personal = source === 'tencent-docs' ? catalog?.mine?.[0] : undefined;
  const collections = libraryCollections(catalog, local ? 'all' : category).filter(item => item.id !== personal?.id);
  const directoryRows = spaceList ? spaces : collections;
  const visible = (directory ? (catalogSearch && query.trim()
    ? directoryRows.filter(item => [title(item), item.path].join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
    : directoryRows) : rows) as any[];
  const categoryTitle = local ? '项目资料' : category === 'mine' ? labels.mine
    : category === 'spaces' ? labels.groups : category === 'shared' ? '共享知识库' : category.startsWith('org:')
      ? spaces.find(item => 'org:' + item.id === category)?.name || labels.groups : labels.all;
  const location = parents.at(-1) || collection;
  const heading = title(location || {title: categoryTitle});
  const folder = (item: any) => directory || Boolean(item.is_folder);
  const itemCollection = (item: any) => String(item.collection_id ?? item.knowledge_base_id ?? collection?.id ?? '');
  const pathId = (item: any) => source === 'weknora' ? String(item.folder_path ?? item.path ?? libraryItemId(item)) : libraryItemId(item);
  const referenceable = (item: any) => !folder(item) && item.readable !== false && Boolean(libraryItemId(item));
  const canPick = (item: any) => !directory && (publicFiles || referenceable(item));
  const pickable = visible.filter(canPick), chosen = visible.filter(item => selection.has(libraryItemKey(item)));
  const selectedCount = pickable.filter(item => selection.has(libraryItemKey(item))).length;
  const batch = chosen.length > 0;
  const namingKey = naming ? (naming.item ? libraryItemKey(naming.item) : 'new') : '';
  const weknoraWrite = collection?.group === 'mine' || !collection?.source_from_agent && ['editor', 'admin'].includes(collection?.permission);
  const remoteUpload = !local && collection && !searching && (source !== 'weknora' || weknoraWrite) &&
    !parents.some((item: any) => item.writeable === false)
    && collection.writeable !== false && (!parents.length || collection.nested_writeable !== false)
    && !['read', 'reader', 'viewer', 'read_only'].includes(collection.permission);
  const manualWrite = source === 'weknora' && remoteUpload && (!collection.type || collection.type === 'document');
  const showSpace = directory && !local && source !== 'tencent-docs' && category === 'all';
  const showPath = local && (directory || searching);
  const showType = !directory && (source === 'tencent-docs' || source === 'lexiang');
  const showOrigin = searching && !local && source !== 'tencent-docs';
  const detailColumn = showSpace || showPath || showType || showOrigin;
  const recentVisits = visits.filter(visit => visit.source === 'local' || Boolean(visit.identity &&
    visit.identity === libraryIdentity(status?.sources?.[visit.source])));

  function updateVisits(change: (current: LibraryVisit[]) => LibraryVisit[]) {
    const next = change(host.get('library:visits', visits));
    setVisits(next);
    try {saveLibraryVisits(next);}
    catch {setNotice({text:'最近使用记录暂未写入本机'});}
  }

  function remember(item: any, target = collection, trail = parents, route = category) {
    if (!target?.id || !libraryItemId(item)) return;
    const identity = libraryIdentity(status?.sources?.[source]);
    if (!local && !identity) return;
    const node = (value: any) => ({id:libraryItemId(value), title:title(value), is_folder:Boolean(value.is_folder),
      ...(value.folder_path !== undefined ? {folder_path:value.folder_path} : {})});
    const visit: LibraryVisit = {
      source, identity, category:route, collection:String(target.id), collectionName:title(target),
      item:{...node(item), relative:item.relative, readable:item.readable},
      parents:trail.map(node), time:Date.now(),
    };
    const key = libraryVisitKey(visit);
    updateVisits(previous => [visit, ...previous.filter(entry => libraryVisitKey(entry) !== key)].slice(0,100));
  }

  async function loadStatus() {
    try {
      const value = await rpc('library.status');
      if (!alive.current) return;
      const account = value.sources[source];
      const identity = account && !account.unavailable
        ? JSON.stringify([account.connection_id, account.user_id, account.tenant_id, account.generation]) : '';
      if (!local && view.identity !== identity) {
        clearSelection(); leavePreview(); setNotice(null);
        setEditor(null);
        setView({...emptyView, identity});
      }
      setStatus(value);
    } catch (error) { if (alive.current) setFailure(errorText(error)); }
  }
  useEffect(() => {
    alive.current = true;
    void loadStatus();
    return () => { alive.current = false; pending.current = false; ++generation.current; readRequest.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(timer);
  }, [notice]);
  const nameChanged = Boolean(naming && naming.value !== naming.initial);
  function defaultName(extension?: 'md' | 'txt') {return extension ? '未命名.' + extension : '未命名文档';}
  function changedDocument(draft: DocumentDraft, text: string) {
    return text !== draft.original || !draft.item && draft.name !== defaultName(draft.extension);
  }
  useLayoutEffect(() => {
    if (!registerLeave) return;
    registerLeave(async () => {
      if (pending.current) return !(editor || naming);
      const text = editor ? await documentText(editor) : '';
      if (text === undefined) return false;
      return !(nameChanged || editor && changedDocument(editor, text)) ||
        await confirm('保留资料草稿？', '返回资料库后可继续编辑。', ['继续编辑', '保留并离开']) === 1;
    });
    return () => registerLeave();
  }, [editor, naming, nameChanged, registerLeave, confirm]);
  useLayoutEffect(() => {
    const field = nameInput.current;
    if (!field || !namingKey) return;
    field.focus();
    field.setSelectionRange(0, naming?.item && !naming.item.is_folder ? suffixStart(field.value) : field.value.length);
  }, [namingKey]);
  useLayoutEffect(() => {
    if (allInput.current) allInput.current.indeterminate = selectedCount > 0 && selectedCount < pickable.length;
  }, [selectedCount, pickable.length, preview, recycle]);

  function clearSelection() {
    setSelection(new Set());
    anchor.current = '';
    dismissMenu();
  }
  function choose(item: any, toggle = false, range = false) {
    const key = libraryItemKey(item);
    if (range && canPick(item)) {
      const ordered = pickable.map(libraryItemKey), first = ordered.indexOf(anchor.current), last = ordered.indexOf(key);
      if (first >= 0 && last >= 0) {
        setSelection(new Set(ordered.slice(Math.min(first, last), Math.max(first, last) + 1)));
        return;
      }
    }
    anchor.current = key;
    if (!toggle || !canPick(item)) setSelection(new Set([key]));
    else setSelection(previous => {
      const next = new Set([...previous].filter(id => pickable.some(entry => libraryItemKey(entry) === id)));
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }
  function focusRow(item: any) {
    const key = libraryItemKey(item);
    requestAnimationFrame(() => {
      [...(scroller.current?.querySelectorAll<HTMLElement>('[data-library-row]') || [])]
        .find(node => node.dataset.libraryRow === key)?.querySelector<HTMLButtonElement>('.library-item-name')?.focus({preventScroll:true});
    });
  }
  function leavePreview() {
    readRequest.current?.abort();
    readRequest.current = null;
    setReading(false); setReadJob(null); setPreview(null); setFailure('');
  }
  async function perform<T>(action: (ticket: number) => Promise<T>, onFailure: (message: string) => void = setFailure): Promise<T | undefined> {
    if (!alive.current || pending.current) return;
    const ticket = ++generation.current;
    pending.current = true;
    setBusy(true); setFailure('');
    try {
      const result = await action(ticket);
      return ticket === generation.current ? result : undefined;
    } catch (error) {
      if (ticket === generation.current) onFailure(errorText(error));
    } finally {
      if (ticket === generation.current) {
        pending.current = false;
        setBusy(false); setJob(null);
      }
    }
  }
  const execute = <T = any,>(method: string, params: any = {}, onFailure?: (error: string) => void) =>
    perform<T>(ticket => operation(method, {source, ...params}, progress => {if (ticket === generation.current) setJob(progress);}), onFailure);
  const mutatePublic = async (method: string, params: any, onFailure?: (error: string) => void) => {
    if (pending.current) return;
    update({refreshRequired:true});
    const result = await execute(method, params, onFailure);
    host.set(publicRevisionKey, host.get(publicRevisionKey, 0) + 1);
    return result;
  };
  async function catalogLoad(refresh = false) {
    const result = await execute('library.catalog', {refresh});
    if (result) update({catalog:result});
    return result;
  }
  async function children(target = collection, trail = parents, nextPage = 1, term = '', preserve = false) {
    if (pending.current || (!target?.id && !term)) return false;
    leavePreview();
    if (nextPage === 1 && !preserve) clearSelection();
    else dismissMenu();
    const previousScroll = preview || recycle ? view.scroll || 0 : scroller.current?.scrollTop || 0;
    const publicRevision = host.get(publicRevisionKey, 0);
    const value = await execute(term ? 'library.search' : 'library.children', {
      collection:target?.id || '', parent:trail.length ? pathId(trail.at(-1)) : '', page:nextPage, ...(term ? {query:term} : {}),
    });
    if (!value) return false;
    const items = term ? (local ? value.items : list(value)) : local || source === 'weknora' ? value.items : value[0];
    const nextRows = appendLibraryRows(nextPage > 1 ? rows : [], items);
    const scroll = preserve || nextPage > 1 ? previousScroll : 0;
    update({collection:target, parents:trail, page:nextPage, rows:nextRows,
      more:term && !local ? false : local || source === 'weknora' ? value.has_more : Boolean(value[1]),
      loaded:true, searching:Boolean(term), searchTerm:term, unavailable:value.unavailable || [], total:value.total, scroll,
      ...(nextPage === 1 ? {refreshRequired:false, publicRevision} : {})});
    if (preserve) {
      const valid = new Set(nextRows.map(libraryItemKey));
      setSelection(previous => new Set([...previous].filter(key => valid.has(key))));
    }
    requestAnimationFrame(() => { if (scroller.current) scroller.current.scrollTop = scroll; });
    return nextRows;
  }
  useEffect(() => {
    if (!connected) return;
    void (async () => {
      const result = await catalogLoad();
      if (!result || !local) return;
      const retained = collection && [result.public, ...(result.mine || [])].some(item => item?.id === collection.id);
      const target = retained ? collection : !view.loaded ? result.public : null;
      update({projectsKey, loaded:true, ...(retained ? {} : {collection:target, parents:[], rows:[], more:false, page:1})});
      const publicChanged = target?.id === 'public' && view.publicRevision !== host.get(publicRevisionKey, 0);
      if (target && (!view.loaded || view.projectsKey !== projectsKey || view.refreshRequired || publicChanged)) {
        await children(target, retained ? parents : [], 1, retained ? searchTerm : '', retained);
      }
    })();
  }, [connected, projectsKey, view.identity]);
  useLayoutEffect(() => {
    if (scroller.current && !preview && !recycle) scroller.current.scrollTop = view.scroll || 0;
  }, [Boolean(preview), Boolean(recycle)]);

  async function discardName() {
    if (pending.current) return false;
    if (editor && !await discardDocument()) return false;
    if (nameChanged && await confirm('放弃当前命名？', '名称尚未保存。', ['继续编辑', '放弃修改']) !== 1) return false;
    setNaming(null);
    return true;
  }
  async function selectCollection(item: any) {
    if (!await discardName()) return;
    setRecent(false);
    setRecycle(null);
    update({query:''});
    await children(item, []);
  }
  async function all(nextCategory = category) {
    if (!await discardName()) return;
    setRecent(false);
    clearSelection(); leavePreview(); setRecycle(null);
    update({category:nextCategory, query:'', searchTerm:'', collection:null, parents:[], rows:[], page:1, more:false, searching:false, scroll:0, unavailable:[], loaded:true});
  }
  async function refresh() {
    if (recycle) { await showRecycle(); return; }
    if (!await discardName()) return;
    const current = await catalogLoad(true);
    if (current && (collection || searchTerm)) await children(collection, parents, 1, searchTerm, true);
  }
  async function browse(item: any) {
    if (pending.current || naming) return;
    if (spaceList) { await all('org:' + item.id); return; }
    if (directory) { await selectCollection(item); return; }
    if (!item.is_folder) {
      if (item.readable !== false) {
        const target = collection || libraryCollections(catalog).find(entry => String(entry.id) === itemCollection(item));
        await read(item, target);
      }
      else if (platformUrl(item)) await external(item);
      else setFailure('这份资料需要在原平台打开。');
      return;
    }
    await browseChildren(item);
  }
  async function browseChildren(item: any) {
    const id = itemCollection(item);
    const target = collection?.id === id ? collection : [...collections, personal].find(entry => entry?.id === id);
    if (!target) {
      if (!local && platformUrl(item)) await external(item);
      else setFailure('无法确认所属目录，请从资料来源中打开。');
      return false;
    }
    const trail = itemTrail(item, target);
    update({query:''});
    if (!await children(target, [...trail, item])) return false;
    remember(item, target, trail);
    return true;
  }
  function itemTrail(item: any, target: any) {
    if (!searching) return parents;
    const path = local ? String(item.relative || '').replace(/\\/g,'/').split('/').slice(0,-1).join('/')
      : source === 'weknora' ? item.folder_path : '';
    if (!path) return [];
    const parts = path.split('/').filter(Boolean);
    return parts.map((part: string, index: number) => {
      const id = parts.slice(0,index+1).join('/');
      return {id, title:part, is_folder:true, collection_id:target?.id, folder_path:id};
    });
  }
  const selectionFor = (item: any): LibrarySelection => ({collection:itemCollection(item), item:libraryItemId(item), title:title(item)});
  async function reference(entries: any[], whole = false) {
    if (!entries.length || !whole && !entries.every(referenceable)) return;
    const items = entries.map(item => whole ? {collection:String(item.id), item:'', title:title(item)} : selectionFor(item));
    const result = await perform(async () => { await onReference(source, items); return true; });
    if (result) setNotice({text:entries.length === 1 ? '已添加到当前对话：' + title(entries[0]) : '已添加 ' + entries.length + ' 份资料到当前对话'});
  }
  async function read(item: any, target = collection, trail = itemTrail(item, target), route = category) {
    leavePreview(); dismissMenu();
    const request = new AbortController();
    readRequest.current = request;
    setReading(true); setPreview({...item, data:null});
    try {
      const data = await operation('library.read', {source, ...selectionFor(item)}, value => {
        if (!request.signal.aborted) setReadJob(value);
      }, request.signal);
      if (!request.signal.aborted && alive.current) {
        setPreview({...item, data});
        remember(item, target, trail, route);
      }
    } catch (error) {
      if (!request.signal.aborted && alive.current) setFailure(errorText(error));
    } finally {
      if (!request.signal.aborted && alive.current) {setReading(false); setReadJob(null);}
    }
  }
  async function showRecent() {
    if (!await discardName()) return;
    clearSelection(); leavePreview(); setRecycle(null); setRecent(true);
    void loadStatus();
  }
  async function openRecent(visit: LibraryVisit, locationOnly = false) {
    if (pending.current) return;
    if (visit.source !== source) {
      setRecentTarget({visit, location:locationOnly});
      setSource(visit.source);
      return;
    }
    const entries = libraryCollections(catalog, local ? 'all' : visit.category);
    const target = [catalog?.public, personal, ...entries].find(entry => String(entry?.id) === visit.collection);
    if (!target || !local && visit.identity !== libraryIdentity(status?.sources?.[source])) {
      setFailure('此资料已不可用，请在原位置核对后重试。');
      return;
    }
    const item = {...visit.item, collection_id:visit.collection};
    const trail = visit.parents;
    const openFolder = item.is_folder && !locationOnly;
    const loaded = await children(target, openFolder ? [...trail, item] : trail);
    if (!loaded) return;
    update({category:visit.category, query:''});
    setRecent(false);
    if (openFolder) remember(item,target,trail,visit.category);
    else if (!locationOnly) await read(loaded.find(row => libraryItemId(row) === visit.item.id) || item,target,trail,visit.category);
    else focusRow(item);
  }
  useEffect(() => {
    if (!recentTarget || recentTarget.visit.source !== source || !connected || !catalog || busy) return;
    setRecentTarget(null);
    void openRecent(recentTarget.visit, recentTarget.location);
  }, [recentTarget, connected, catalog, busy]);
  const platformUrl = (item: any) => item.data?.url || item.url;
  async function external(item: any, reveal = false) {
    await perform(() => local ? rpc(reveal ? 'library.reveal' : 'library.external', {source, ...selectionFor(item)})
      : rpc('external.open', {url:platformUrl(item)}));
  }
  function canEdit(item: any) {
    return !folder(item) && (local ? item.editable : manualWrite && item.type === 'manual');
  }
  async function edit(item: any) {
    if (!canEdit(item) || editor) return;
    const file = await execute<FileSession>('library.edit_open', selectionFor(item));
    if (!file) return;
    const text = file.payload.kind === 'sheet' ? JSON.stringify({kind:'sheet', value:JSON.stringify(file.payload.snapshot)})
      : file.payload.content || '';
    setEditor({name:title(item),text,original:text,item,file,manual:!local});
  }
  function startDocument(extension?: 'md' | 'txt') {
    if (pending.current || naming || (extension ? !publicFiles : !manualWrite)) return;
    clearSelection();
    setEditor({name:defaultName(extension),text:'',original:'',extension,manual:!extension});
  }
  function documentText(draft: DocumentDraft) {
    return editorFlush.current ? perform(editorFlush.current) : Promise.resolve(draft.text);
  }
  async function discardDocument() {
    if (!editor) return true;
    if (pending.current) return false;
    const text = await documentText(editor);
    if (text === undefined) return false;
    if (changedDocument(editor, text) &&
        await confirm('放弃未保存的修改？', '当前修改还没有保存。', ['继续编辑', '放弃修改']) !== 1) return false;
    setEditor(null);
    setFailure('');
    return true;
  }
  async function saveDocument() {
    if (!editor || pending.current) return;
    const draft = editor;
    const text = await documentText(draft);
    if (text === undefined) return;
    const filename = draft.name.trim();
    if (!filename || draft.manual && !text.trim()) {setFailure(draft.manual ? '请输入文档标题和正文。' : '请输入文件名。');return;}
    const name = draft.extension && !filename.toLowerCase().endsWith('.' + draft.extension) ? filename + '.' + draft.extension : filename;
    const target = draft.item ? itemCollection(draft.item) : collection?.id;
    const parent = parents.length ? pathId(location) : '';
    let payload: string | object = text;
    try {if (draft.file?.payload.kind === 'sheet') payload = JSON.parse(JSON.parse(text).value);}
    catch (error) {setFailure(errorText(error));return;}
    const change = {collection:target, parent, name, text, payload, item:draft.item ? libraryItemId(draft.item) : undefined, fingerprint:draft.file?.fingerprint};
    const method = draft.item ? 'library.edit_save' : draft.manual ? 'library.create_manual' : 'library.create_file';
    const result = await (local ? mutatePublic(method,change) : execute(method,change));
    if (!result) return;
    setEditor(null);
    setNotice(result.notice ? {text:result.notice} : null);
    if (collection) {
      update({query:''});
      await children(collection, result.at_root ? [] : parents);
    }
    await read(result,collection,result.at_root ? [] : parents);
  }
  function capture(entries: any[], mode: Clipboard['mode']) {
    setClipboard({mode,items:entries.map(item => ({item:libraryItemId(item),revision:item.revision}))});
    setNotice({text:(mode === 'move' ? '已剪切 ' : '已复制 ') + entries.length + ' 项'});
  }
  async function paste(target = location) {
    if (!publicFiles || !clipboard || pending.current) return;
    const result = await mutatePublic('library.paste', {collection:'public', parent:target?.id === 'public' ? '' : pathId(target), ...clipboard});
    if (!result) return;
    if (clipboard.mode === 'move') {
      const moved = new Set(result.moved);
      const items = clipboard.items.filter(item => !moved.has(item.item));
      setClipboard(items.length ? {...clipboard,items} : null);
      if (moved.size) updateVisits(previous => previous.filter(visit => visit.source !== 'local' ||
        !moved.has(visit.item.id) && !visit.parents.some(parent => moved.has(parent.id))));
    }
    await children(collection,parents,1,searchTerm,true);
    if (result.added.length) setNotice({text:(clipboard.mode === 'move' ? '已移动 ' : '已复制 ') + result.added.length + ' 项'});
    reportFailures(result.failed);
  }
  async function importFiles(paths: string[]) {
    if (!paths.length || !publicFiles || naming) return;
    const result = await mutatePublic('library.import', {collection:'public', parent:parents.length ? pathId(location) : '', paths});
    if (!result) return;
    await children(collection, parents, 1, searchTerm, true);
    if (result.added.length) setNotice({text:'已添加 ' + result.added.length + ' 个文件'});
    reportFailures(result.failed);
  }
  async function chooseFiles() {
    try {
      if (local) await importFiles(await rpc('dialog.choose', {kind:'files'}) || []);
      else if (remoteUpload) {
        const path = await rpc('dialog.choose', {kind:'file'});
        if (!path) return;
        const result = await execute('library.upload', {path, collection:collection.id, parent:parents.length ? pathId(location) : ''});
        if (result) {
          setNotice({text:source === 'lexiang' && result.status === 'saved' ? '文件已上传' : '上传已提交，可在后台操作中核对结果。'});
          await children(collection, parents, 1, '', true);
        }
      }
    } catch (error) {setFailure(errorText(error));}
  }
  const openFolder = () => execute('library.open_folder', {collection:collection.id, parent:parents.length ? pathId(location) : ''});

  async function startName(item?: any) {
    if (!publicFiles || pending.current) return;
    if (naming) { nameInput.current?.focus(); return; }
    if (!item && searching) {
      if (!await children(collection, parents, 1)) return;
      update({query:''});
    }
    let name = item ? title(item) : '新建文件夹';
    if (!item) {
      const names = new Set(visible.map(row => title(row).toLocaleLowerCase()));
      for (let index = 2; names.has(name); index++) name = '新建文件夹 (' + index + ')';
    }
    clearSelection();
    setNaming({item, initial:name, value:name});
  }
  function cancelName() {
    if (!naming || pending.current) return;
    if (naming.item) focusRow(naming.item);
    else requestAnimationFrame(() => newFolderButton.current?.focus());
    setNaming(null);
  }
  async function saveName() {
    if (!naming) return;
    const draft = naming;
    const target = await mutatePublic(draft.item ? 'library.rename' : 'library.mkdir', {
      collection:'public', parent:parents.length ? pathId(location) : '',
      item:draft.item ? libraryItemId(draft.item) : undefined, revision:draft.item?.revision, name:draft.value,
    }, message => setNaming(current => current && {...current, error:message}));
    if (!target) return;
    setNaming(null);
    const nextRows = draft.item ? rows.map((row: any) => libraryItemKey(row) === libraryItemKey(draft.item) ? target : row) : [target, ...rows];
    update({rows:nextRows});
    if (draft.item && libraryItemId(draft.item) !== libraryItemId(target)) {
      const id = libraryItemId(draft.item);
      updateVisits(previous => previous.filter(visit => visit.source !== 'local' || visit.item.id !== id && !visit.parents.some(parent => parent.id === id)));
    }
    if (await children(collection, parents, 1, searchTerm, true)) focusRow(target);
    remember(target);
  }
  function reportFailures(items: {name: string; reason: string}[]) {
    if (items.length) setFailure(items.map(item => item.name + '：' + item.reason).join('。'));
  }
  async function trash(entries: any[]) {
    if (!publicFiles || !entries.length || pending.current || naming) return;
    const includesFolders = entries.some(item => item.is_folder);
    if (entries.length > 1 || includesFolders) {
      const listed = entries.slice(0, 6).map(title).join('、');
      const detail = listed + (entries.length > 6 ? ' 等 ' + entries.length + ' 项' : '') + '将移到公共资料回收站。'
        + (includesFolders ? '文件夹内的内容一并移入。' : '');
      if (await confirm('移到回收站？', detail, ['取消', '移到回收站']) !== 1 || !alive.current) return;
    }
    const result = await mutatePublic('library.trash', {collection:'public', items:entries.map(item => ({item:libraryItemId(item), revision:item.revision}))});
    if (!result) return;
    const moved = new Set(result.moved);
    const remaining = rows.filter((item: any) => !moved.has(libraryItemId(item)));
    update({rows:remaining});
    const failed = entries.filter(item => !moved.has(libraryItemId(item)));
    setSelection(previous => new Set([...previous].filter(key => failed.some(item => libraryItemKey(item) === key))));
    anchor.current = '';
    if (result.moved.length) {
      updateVisits(previous => previous.filter(visit => visit.source !== 'local' ||
        !moved.has(visit.item.id) && !visit.parents.some(parent => moved.has(parent.id))));
      await children(collection, parents, 1, searchTerm, true);
      setNotice({text:'已将 ' + result.moved.length + ' 项移到回收站', undo:result.id});
    }
    reportFailures(result.failed);
  }
  async function showRecycle() {
    if (!await discardName()) return;
    const records = await execute<RecycleBatch[]>('library.trash_list');
    if (records) { leavePreview(); clearSelection(); setRecycle(records); }
  }
  async function closeRecycle() {
    if (view.refreshRequired && !await children(collection, parents, 1, searchTerm, true)) return;
    setRecycle(null);
  }
  async function restore(id: string) {
    const result = await mutatePublic('library.restore', {id});
    if (!result) return;
    if (recycle) await showRecycle();
    else if (collection && result.restored.length) {
      const updated = await children(collection, parents, 1, searchTerm, true);
      const restored = new Set(result.restored);
      if (updated) {
        const first = updated.find(item => restored.has(libraryItemId(item)));
        if (first) focusRow(first);
      }
    }
    if (result.restored.length) setNotice({text:'已还原 ' + result.restored.length + ' 项'});
    reportFailures(result.failed);
  }

  function showMenu(item: any | undefined, trigger: HTMLButtonElement | null, point?: {x: number; y: number}) {
    if (busy || naming || !item && !publicFiles) return;
    const items = item && selection.has(libraryItemKey(item)) ? chosen : item ? [item] : [];
    const rect = trigger?.getBoundingClientRect();
    setMenu({row:item, items, anchor:{x:point?.x ?? rect?.left ?? 0, below:point?.y ?? rect?.bottom ?? 0, above:point?.y ?? rect?.top ?? 0, trigger}});
  }
  function openMenu(kind: Exclude<Menu['kind'], 'recent' | undefined>, trigger: HTMLButtonElement) {
    const rect = trigger.getBoundingClientRect();
    setMenu(menu?.kind === kind ? null : {kind,items:[],anchor:{x:rect.left, below:rect.bottom, above:rect.top, trigger}});
  }
  async function switchSource(next: string) {
    if (!await discardName()) return;
    setRecent(false); setRecentTarget(null); setSource(next);
  }
  async function goUp() {
    if (!await discardName()) return;
    if (parents.length) {
      update({query:''});
      if (await children(collection,parents.slice(0,-1))) focusRow(parents.at(-1));
    } else await all(!collection && category.startsWith('org:') ? 'spaces' : category);
  }
  function keys(event: KeyboardEvent<HTMLElement>) {
    const target = event.target as HTMLElement;
    if (editor && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {event.preventDefault();void saveDocument();return;}
    if (editor && event.key === 'Escape') {event.preventDefault();void discardDocument();return;}
    if (target.closest('input:not([type="checkbox"]), textarea, select, [contenteditable="true"]') || busy || naming || recycle || preview || editor || recent) return;
    const command = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
    const index = visible.findIndex(item => libraryItemKey(item) === target.closest<HTMLElement>('[data-library-row]')?.dataset.libraryRow);
    const item = visible[index] || chosen[0];
    if (key === 'escape' && selection.size) {
      event.preventDefault(); clearSelection();
    } else if (command && key === 'a' && pickable.length) {
      event.preventDefault(); dismissMenu(); setSelection(new Set(pickable.map(libraryItemKey)));
    } else if (command && ['c','x'].includes(key) && publicFiles && (chosen.length || item) && !window.getSelection()?.toString()) {
      event.preventDefault(); capture(chosen.length ? chosen : [item],key === 'x' ? 'move' : 'copy');
    } else if (command && key === 'v' && publicFiles && clipboard) {
      event.preventDefault(); void paste();
    } else if (command && event.shiftKey && key === 'n' && publicFiles) {
      event.preventDefault(); void startName();
    } else if (command && key === 'z' && publicFiles && notice?.undo) {
      event.preventDefault(); void restore(notice.undo);
    } else if (key === 'f2' && publicFiles && item && chosen.length <= 1) {
      event.preventDefault(); void startName(chosen[0] || item);
    } else if (key === 'delete' && publicFiles && (chosen.length || item) && !event.shiftKey) {
      event.preventDefault(); void trash(chosen.length ? chosen : [item]);
    } else if ((event.shiftKey && key === 'f10') || key === 'contextmenu') {
      if (!item && !publicFiles) return;
      event.preventDefault(); showMenu(item, target.closest('button'));
    } else if (key === 'enter' && index >= 0 && target.matches('.library-item-name')) {
      event.preventDefault();
      void browse(visible[index]);
    } else if (command && key === ' ' && item && canPick(item)) {
      event.preventDefault(); choose(item, true);
    } else if (event.altKey && ['arrowleft', 'arrowup'].includes(key) && (parents.length || collection && !publicFiles || category.startsWith('org:'))) {
      event.preventDefault(); void goUp();
    } else if (key === 'f5') {
      event.preventDefault(); void refresh();
    } else if (key === 'f3') {
      event.preventDefault(); searchInput.current?.focus();
    } else if (index >= 0 && ['arrowup', 'arrowdown'].includes(key)) {
      const next = visible[Math.max(0, Math.min(index + (key === 'arrowup' ? -1 : 1), visible.length - 1))];
      event.preventDefault();
      focusRow(next);
    }
  }

  function menuEntry(Icon: typeof Plus, label: string, action: () => void, shortcut?: string, dangerous = false) {
    return <button type="button" role="menuitem" className={dangerous ? 'error-text' : undefined} onClick={() => {
      dismissMenu(); action();
    }}><Icon size={15}/><span>{label}</span>{shortcut && <kbd>{shortcut}</kbd>}</button>;
  }
  const clipboardControls = (items: any[]) => publicFiles && items.length > 0 && <>
    {menuEntry(Scissors, '剪切', () => capture(items,'move'), 'Ctrl+X')}
    {menuEntry(Copy, '复制', () => capture(items,'copy'), 'Ctrl+C')}
  </>;
  const recentMenu = menu?.kind === 'recent' ? menu.row as LibraryVisit : null;
  const renderedMenu = menu && createPortal(<SidebarMenu anchor={menu.anchor} onClose={dismissMenu}
    className={menu.kind === 'source' ? 'library-source-menu' : 'library-menu'}
    matchTriggerWidth={menu.kind === 'source'}
    label={menu.kind === 'source' ? '选择资料空间' : menu.kind === 'create' ? '新建' : menu.items.length > 1 ? '所选资料操作' : '资料操作'}
    first={menu.kind === 'source' ? sources.map(([id,name]) => {
      const Icon = sourceIcon(id);
      return <button key={id} role="menuitemradio" aria-checked={source === id} onClick={() => {dismissMenu();void switchSource(id);}}>
        <Icon size={16}/><span>{name}</span>{source === id && <Check size={15}/>}
      </button>;
    }) : menu.kind === 'create' ? <>
      {menuEntry(FileText, 'Markdown 文档', () => startDocument('md'), '.md')}
      {menuEntry(FileText, '纯文本', () => startDocument('txt'), '.txt')}
      <div className="sidebar-menu-separator" role="separator"/>
      {menuEntry(FolderPlus, '文件夹', () => void startName(), 'Ctrl+Shift+N')}
    </> : recentMenu ? <>
      {menuEntry(FolderOpen, '打开', () => void openRecent(recentMenu), 'Enter')}
      {menuEntry(MapPin, '打开所在位置', () => void openRecent(recentMenu,true))}
      {menuEntry(X, '从最近移除', () => updateVisits(entries => entries.filter(item => libraryVisitKey(item) !== libraryVisitKey(recentMenu))))}
    </> : <>
      {menu.row && menu.items.length === 1 && <>
        {menuEntry(menu.row.is_folder ? FolderOpen : FileText, '打开', () => void browse(menu.row), 'Enter')}
        {publicFiles && menu.row.is_folder && menuEntry(FolderPlus, '在此新建', () => void browseChildren(menu.row).then(opened => {
          if (opened && newFolderButton.current) openMenu('create', newFolderButton.current);
        }))}
        {source === 'lexiang' && !menu.row.is_folder && menu.row.has_children && menuEntry(Folder, '浏览子页面', () => void browseChildren(menu.row))}
        {canEdit(menu.row) && menuEntry(Pencil, '编辑', () => void edit(menu.row))}
      </>}
      {menu.items.length > 0 && menu.items.every(referenceable) && menuEntry(Plus, '添加到当前对话', () => void reference(menu.items))}
      {clipboardControls(menu.items)}
      {menu.row && menu.items.length === 1 && <>
        {publicFiles && menu.row.is_folder && clipboard && menuEntry(ClipboardPaste, '粘贴到此文件夹', () => void paste(menu.row))}
        {publicFiles && menuEntry(Pencil, '重命名', () => void startName(menu.row), 'F2')}
        {local && (menu.row.is_folder
          ? menuEntry(ExternalLink, '在资源管理器中打开', () => void execute('library.open_folder', {collection:itemCollection(menu.row),parent:pathId(menu.row)}))
          : menuEntry(ExternalLink, '在本机打开', () => void external(menu.row)))}
        {local && menuEntry(MapPin, '在资源管理器中显示', () => void external(menu.row, true))}
        {!local && platformUrl(menu.row) && menuEntry(ExternalLink, source === 'weknora' ? '在 WeKnora 打开知识库' : '在原平台打开', () => void external(menu.row))}
      </>}
      {!menu.row && <>
        {clipboard && menuEntry(ClipboardPaste, '粘贴', () => void paste(), 'Ctrl+V')}
        {menuEntry(FolderPlus, '新建文件夹', () => void startName(), 'Ctrl+Shift+N')}
        {menuEntry(RefreshCw, '刷新', () => void refresh(), 'F5')}
      </>}
    </>} last={!menu.kind && publicFiles && menu.items.length > 0 && menuEntry(Trash2, '移到回收站', () => void trash(menu.items), 'Del', true)}/>, document.body);

  const searchBox = !preview && !recycle && !editor && !recent && <form className="page-search library-search" role="search" onSubmit={event => {
    event.preventDefault();
    if (catalogSearch || !collection && !query.trim()) return;
    void children(collection, parents, 1, query.trim());
  }}>
    <input ref={searchInput} type="search" aria-label="搜索资料" placeholder={spaceList ? '搜索' + labels.groups
      : local ? (directory ? '搜索项目' : '搜索文件名') : source === 'weknora' && collection ? '搜索库内资料' : labels.search}
      value={query} disabled={Boolean(naming)} onChange={event => {
        const value = event.target.value;
        update({query:value});
        if (searching && !value.trim()) {
          if (collection) void children(collection, parents, 1);
          else void all();
        }
      }}/>
    <button className="icon-button" type="submit" aria-label="搜索" disabled={busy || Boolean(naming)}><Search size={17}/></button>
  </form>;
  const nameEditor = naming && <form className="library-row library-naming" onSubmit={event => {event.preventDefault(); void saveName();}}
    onKeyDown={event => {if (event.key === 'Escape') {event.stopPropagation(); cancelName();}}}>
    <div>
      {!naming.item || naming.item.is_folder ? <Folder size={17}/> : <FileText size={17}/>}
      <input ref={nameInput} required value={naming.value} readOnly={busy}
        aria-label={naming.item ? '新名称' : '文件夹名称'} aria-invalid={Boolean(naming.error)} aria-describedby={naming.error ? 'library-name-error-' + scope : undefined}
        onChange={event => setNaming({...naming, value:event.target.value, error:undefined})}/>
      <button type="submit" className="icon-button" aria-label="确认名称" title="确认名称 · Enter" disabled={busy}><Check size={17}/></button>
      <button type="button" className="icon-button" aria-label="取消命名" title="取消命名 · Esc" disabled={busy} onClick={cancelName}><X size={17}/></button>
    </div>
    {naming.error && <p role="alert" className="error-text" id={'library-name-error-' + scope}>{naming.error}</p>}
  </form>;
  const folderInsert = visible.findIndex(item => !item.is_folder);

  const SourceIcon = sourceIcon(source);
  const breadcrumb = !preview && !recycle && !editor && !recent && (parents.length > 0 || collection && !publicFiles || category.startsWith('org:'));
  return <div className="library-space">
    <nav className="library-spaces" aria-label="资料空间">
      <button className={'library-recent' + (recent ? ' selected' : '')} aria-current={recent ? 'page' : undefined}
        onClick={() => void showRecent()} disabled={busy}><Clock3 size={16}/>最近</button>
      <div className="library-source">
        <button type="button" className="library-source-select" aria-label={'选择资料空间 · ' + sourceName} aria-haspopup="menu"
          aria-expanded={menu?.kind === 'source'} disabled={busy}
          onKeyDown={event => {if (event.key === 'ArrowDown') {event.preventDefault();openMenu('source',event.currentTarget);}}}
          onClick={event => openMenu('source',event.currentTarget)}>
          <SourceIcon size={16}/><span>{sourceName}</span><ChevronDown size={15}/>
        </button>
      </div>
      {connected && <div className="library-collections">
        {local ? <>
          <button className={publicFiles && !recent ? 'selected' : ''} aria-current={publicFiles && !recent ? 'page' : undefined} disabled={busy || !catalog?.public}
            onClick={() => catalog?.public && void selectCollection(catalog.public)}><Folder size={16}/>公共资料</button>
          <button className={!publicFiles && !recent ? 'selected' : ''} aria-current={!publicFiles && !recent ? 'page' : undefined} disabled={busy} onClick={() => void all()}><BookOpen size={16}/>项目资料</button>
        </> : <>
          {personal && <button className={!recent && collection?.id === personal.id ? 'selected' : ''} disabled={busy} onClick={() => void selectCollection(personal)}><Folder size={16}/>{title(personal)}</button>}
          <button className={!recent && category === 'all' && (!collection || collection.id !== personal?.id) ? 'selected' : ''} disabled={busy} onClick={() => void all('all')}><BookOpen size={16}/>{labels.all}</button>
          {labels.mine && <button className={!recent && category === 'mine' ? 'selected' : ''} disabled={busy} onClick={() => void all('mine')}><Folder size={16}/>{labels.mine}</button>}
          {source !== 'tencent-docs' && <button className={!recent && (category === 'spaces' || category.startsWith('org:')) ? 'selected' : ''}
            disabled={busy} onClick={() => void all('spaces')}><Users size={16}/>{labels.groups}</button>}
        </>}
      </div>}
    </nav>
    <section className="library-main" aria-label={sourceName} onKeyDown={keys}>
      {(connected || recent) && <header className="library-heading">
        <div className="library-location">
          {breadcrumb && <button className="icon-button" aria-label="返回上一级" title="返回上一级 · Alt+↑" disabled={busy} onClick={() => void goUp()}><ArrowLeft size={17}/></button>}
          {(preview || recycle || editor) && <button className="icon-button" aria-label="返回列表" title="返回列表" disabled={busy}
            onClick={() => editor ? void discardDocument() : recycle ? void closeRecycle() : leavePreview()}><ArrowLeft size={17}/></button>}
          {breadcrumb && <nav aria-label="当前位置" className="library-breadcrumb">
            {category.startsWith('org:') && <span><button disabled={busy} onClick={() => void all('spaces')}>{labels.groups}</button><ChevronRight size={13}/></span>}
            {collection && !publicFiles && collection.id !== personal?.id && <span><button disabled={busy} onClick={() => void all()}>{categoryTitle}</button><ChevronRight size={13}/></span>}
            {parents.length > 0 && <span><button disabled={busy} onClick={async () => {if (await discardName()) {update({query:''});void children(collection, []);}}}>{title(collection)}</button><ChevronRight size={13}/></span>}
            {parents.slice(0, -1).map((item: any, index: number) => <span key={pathId(item)}>
              <button disabled={busy} onClick={async () => {if (await discardName()) {update({query:''});void children(collection, parents.slice(0, index + 1));}}}>{title(item)}</button><ChevronRight size={13}/>
            </span>)}
            <h2 aria-current="location">{heading}</h2>
          </nav>}
          {editor && !editor.item ? <input className="library-editor-name" aria-label={editor.manual ? '文档标题' : '文件名'} autoFocus value={editor.name} disabled={busy}
            onChange={event => setEditor({...editor, name:event.target.value})}/>
            : !breadcrumb && <h2>{editor?.name || (preview ? title(preview) : recent ? '最近' : recycle ? '回收站' : searching && !local && source !== 'weknora' ? '搜索结果' : heading)}</h2>}
        </div>
        {searchBox}
        {editor && <button type="button" className="primary" disabled={busy} onClick={() => void saveDocument()}><Check size={16}/>保存</button>}
      </header>}
      <JobProgress job={readJob || job}/>
      {failure && <ErrorState message={failure}/>}
      {notice && <div className="library-notice" role="status"><span>{notice.text}</span>
        {notice.undo && <button type="button" className="text-link" disabled={busy} onClick={() => void restore(notice.undo!)}>撤销</button>}
        <button type="button" className="icon-button" aria-label="关闭提示" onClick={() => setNotice(null)}><X size={15}/></button>
      </div>}
      {recent ? <div className="library-list-scroll library-recent-list" data-detail>
        {recentVisits.length > 0 && <div className="library-columns">
          <span className="library-name-heading">名称</span><span className="library-item-meta">位置</span><span className="library-date">最近使用</span>
        </div>}
        {recentVisits.map(visit => {
          const name = visit.item.title, where = [visit.source === 'local' ? '' : sources.find(([id]) => id === visit.source)?.[1],
            visit.collectionName, visit.parents.at(-1)?.title].filter(Boolean).join(' / ');
          const context = (trigger: HTMLButtonElement, event?: MouseEvent) => {
            const rect = trigger.getBoundingClientRect();
            setMenu({kind:'recent', row:visit, items:[], anchor:{x:event?.clientX ?? rect.left, below:event?.clientY ?? rect.bottom,
              above:event?.clientY ?? rect.top, trigger}});
          };
          return <div className="library-row" key={libraryVisitKey(visit)}
            onContextMenu={event => {
              const trigger = event.currentTarget.querySelector<HTMLButtonElement>('.library-row-more');
              if (trigger) {event.preventDefault();context(trigger,event);}
            }}>
            <button type="button" className="library-item-name" title={name} disabled={busy} onClick={() => void openRecent(visit)}>
              {visit.item.is_folder ? <Folder size={17}/> : <FileText size={17}/>}<span className="library-name"><span>{name}</span></span>
            </button>
            <span className="library-item-meta" title={where}>{where}</span>
            <time className="library-date">{new Date(visit.time).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}</time>
            <button type="button" className="icon-button library-row-more" aria-label={'操作 ' + name} aria-haspopup="menu"
              onClick={event => context(event.currentTarget)}><Ellipsis size={17}/></button>
          </div>;
        })}
        {!recentVisits.length && <Empty icon={Clock3} title="暂无最近资料" detail=""/>}
      </div> : !connected ? <div className="library-connection">
        <Empty icon={BookOpen} title={'连接' + sourceName} detail=""/>
        {source === 'tencent-docs' ? <div className="button-row">
          <button className="primary" disabled={busy} onClick={async () => {
            const result = await execute('library.authorize');
            if (result) {setAuthUrl(result.url); await perform(() => rpc('external.open', {url:result.url}));}
          }}><ExternalLink size={16}/>在浏览器授权</button>
          {authUrl && <button className="secondary" disabled={busy} onClick={async () => { if (await execute('library.confirm')) await loadStatus(); }}>已完成授权</button>}
        </div> : <form onSubmit={async event => {
          event.preventDefault();
          if (await execute('library.login', form)) {setForm({...form, password:'', token:''}); await loadStatus();}
        }}>
          <div className="form-grid">{(source === 'weknora' ? [['url', '服务地址', 'url'], ['email', '邮箱', 'email'], ['password', '密码', 'password']]
            : [['company', '企业标识', 'text'], ['token', '访问令牌', 'password']]).map(([key, label, type]) =>
            <Field key={key} label={label} wide><input required type={type} autoComplete="off" value={form[key as keyof typeof form]} onChange={event => setForm({...form, [key]:event.target.value})}/></Field>)}</div>
          <button className="primary" disabled={busy}><Link2 size={16}/>连接</button>
        </form>}
      </div> : <>
        {editor && <div className="library-document library-editor" inert={busy}>
          {editor.file && ['sheet','html'].includes(editor.file.payload.kind) ? <OfficeEditor file={editor.file} text={editor.text}
            onChange={text => setEditor(current => current && {...current,text})} error={onError}
            register={(_, flush) => {editorFlush.current = flush;}}/>
            : <textarea aria-label="正文" placeholder="输入正文…" value={editor.text} autoFocus={Boolean(editor.item)}
              onChange={event => setEditor({...editor,text:event.target.value})}/>}
        </div>}
        {preview && !editor && <div className="library-document">
          <div className="library-document-tools">
            <div className="button-row">
              {canEdit(preview) && <button className="icon-button" title="编辑" aria-label="编辑" disabled={busy} onClick={() => void edit(preview)}><Pencil size={16}/></button>}
              {source === 'lexiang' && preview.has_children && <button className="icon-button" title="浏览子页面" aria-label="浏览子页面" onClick={() => void browseChildren(preview)}><FolderOpen size={16}/></button>}
              {(local || platformUrl(preview)) && <button className="secondary" onClick={() => void external(preview)}><ExternalLink size={15}/>
                {local ? '在本机打开' : source === 'weknora' ? '在 WeKnora 打开知识库' : '在原平台打开'}</button>}
              <button className="primary" disabled={busy || preview.readable === false} onClick={() => void reference([preview])}><Plus size={16}/>添加到当前对话</button>
            </div>
          </div>
          <div className="library-document-content">{preview.data ? <LibraryPreview data={preview.data}/>
            : reading ? <p className="muted" role="status">正在读取资料…</p>
              : <button className="secondary" onClick={() => void read(preview)}>重试</button>}</div>
        </div>}
        {recycle && <div className="library-list-scroll" aria-label="公共资料回收站">
          {!recycle.length && <Empty icon={Trash2} title="回收站为空" detail=""/>}
          {recycle.map(item => <div key={item.id} className="library-recycle-row">
            <div><strong>{item.items.length === 1 ? item.items[0].name : item.items.length + ' 项资料'}</strong>
              <small>{item.items.map(entry => entry.relative).join('、')}</small></div>
            <time>{date(item.time)}</time>
            <button className="secondary" type="button" disabled={busy} onClick={() => void restore(item.id)}><ArchiveRestore size={16}/>还原</button>
          </div>)}
        </div>}
        <div className={'library-list-view' + (dragging ? ' library-dragging' : '')} hidden={Boolean(preview || recycle || editor)}
          onDragOver={event => {if (publicFiles && !busy && !naming && event.dataTransfer.types.includes('Files')) {event.preventDefault(); setDragging(true);}}}
          onDragLeave={event => {if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false);}}
          onDrop={event => {event.preventDefault(); setDragging(false); if (publicFiles && !busy && !naming) void importFiles(Array.from(event.dataTransfer.files).map(file => window.cowork.pathForFile(file)).filter(Boolean));}}>
          <div className="library-toolbar">
            <div className="button-row library-list-actions">
              {publicFiles && <>
                <button type="button" ref={newFolderButton} className="primary" disabled={busy || Boolean(naming)}
                  aria-haspopup="menu" aria-expanded={menu?.kind === 'create'} onClick={event => openMenu('create',event.currentTarget)}><Plus size={16}/>新建<ChevronDown size={15}/></button>
                <button type="button" disabled={busy || Boolean(naming)} onClick={() => void chooseFiles()}><Upload size={16}/>添加文件</button>
                {clipboard && <button type="button" className="icon-button" title="粘贴到当前文件夹" aria-label="粘贴到当前文件夹" disabled={busy} onClick={() => void paste()}><ClipboardPaste size={17}/></button>}
              </>}
              {local && directory && <button className="secondary" onClick={() => addProject(false)}><FolderPlus size={16}/>新建项目</button>}
              {manualWrite && <button className="primary" disabled={busy} onClick={() => startDocument()}><Plus size={16}/>新建文档</button>}
              {remoteUpload && <button className={manualWrite ? undefined : 'primary'} disabled={busy} onClick={() => void chooseFiles()}><Upload size={16}/>上传文件</button>}
              {!local && collection && !parents.length && !searching && <button disabled={busy} onClick={() => void reference([collection], true)}><Link2 size={16}/>
                {personal?.id === collection.id ? '引用个人文件' : labels.collection}</button>}
            </div>
            <div className="button-row library-controls">
              {batch ? <>
                <span className="muted" role="status">已选 {chosen.length} 项</span>
                {chosen.every(referenceable) && <button type="button" disabled={busy} onClick={() => void reference(chosen)}>添加到当前对话</button>}
                {publicFiles && <button type="button" className="icon-button error-text" aria-label="将所选项移到回收站" title="移到回收站 · Del" disabled={busy} onClick={() => void trash(chosen)}><Trash2 size={17}/></button>}
                <button type="button" className="icon-button" aria-label="取消选择" title="取消选择 · Esc" disabled={busy} onClick={() => {clearSelection(); requestAnimationFrame(() => allInput.current?.focus());}}><X size={17}/></button>
              </> : <>
                <button className="icon-button" aria-label="刷新资料" title="刷新 · F5" disabled={busy} onClick={() => void refresh()}><RefreshCw size={17}/></button>
                {publicFiles && <>
                  <button className="icon-button" aria-label="在资源管理器中打开当前目录" title="在资源管理器中打开" disabled={busy} onClick={() => void openFolder()}><ExternalLink size={17}/></button>
                  <button className="icon-button" aria-label="公共资料回收站" title="回收站" disabled={busy} onClick={() => void showRecycle()}><Trash2 size={17}/></button>
                </>}
                {!local && <button className="icon-button" aria-label="断开连接" title="断开连接" disabled={busy} onClick={async () => {
                  if (await confirm('断开' + sourceName + '？', '已选资料仍保留，重新连接后可继续使用。', ['取消', '断开']) === 1
                    && await execute('library.logout')) {
                    clearSelection();
                    setStatus(null);
                    setView({...emptyView, identity:''});
                    await loadStatus();
                  }
                }}><LogOut size={17}/></button>}
              </>}
            </div>
          </div>
          {dragging && <p className="muted" role="status">松开以复制到公共资料</p>}
          {view.unavailable?.length > 0 && <p className="error-text">目录不可用：{view.unavailable.join('、')}。恢复目录后刷新。</p>}
          <div ref={scroller} className="library-list-scroll" data-multi={!directory || undefined} data-detail={detailColumn || undefined} data-space-list={spaceList || undefined}
            onScroll={event => {dismissMenu(); if (!preview && !recycle) update({scroll:event.currentTarget.scrollTop});}}
            onContextMenu={event => {
              if (!publicFiles || (event.target as HTMLElement).closest('input,button,.library-row')) return;
              event.preventDefault(); showMenu(undefined, null, {x:event.clientX, y:event.clientY});
            }}
            onClick={event => {if (event.target === event.currentTarget) clearSelection();}}>
            {!spaceList && (visible.length > 0 || naming) && <div className="library-columns">
              {!directory && <label className="library-check" title={selectedCount === pickable.length && pickable.length > 0 ? '取消全选' : '全选已加载资料 · Ctrl+A'}>
                <input ref={allInput} type="checkbox" aria-label="全选已加载资料" disabled={busy || !pickable.length || Boolean(naming)}
                  checked={pickable.length > 0 && selectedCount === pickable.length}
                  onChange={event => {
                    const checked = event.target.checked;
                    if (checked) setSelection(new Set(pickable.map(libraryItemKey))); else clearSelection();
                    dismissMenu();
                  }}/>
              </label>}
              <span className="library-name-heading">名称</span>
              {detailColumn && <span className="library-item-meta">{showSpace ? (source === 'lexiang' ? '团队' : '所属空间') : showPath ? '位置' : showOrigin ? '知识库' : '类型'}</span>}
              <span className="library-date">修改时间</span>
            </div>}
            {visible.map((item, index) => {
              const key = libraryItemKey(item);
              const selected = selection.has(key);
              const isFolder = folder(item), name = title(item), ext = isFolder ? name.length : suffixStart(name);
              const meta = showPath ? (directory ? item.path : item.relative) : showSpace ? (item.sharedWith?.join('、') || (item.group === 'mine' ? '我的知识库' : ''))
                : showOrigin ? title([...collections, collection].find(entry => entry?.id === itemCollection(item)) || {title:'—'})
                : isFolder ? '' : item.file_type || item.item_type || '';
              const disclosure = (event: MouseEvent<HTMLButtonElement>) => showMenu(item, event.currentTarget);
              return <Fragment key={key}>
                {naming && !naming.item && index === folderInsert ? nameEditor : null}
                {namingKey === key ? nameEditor : <div className={'library-row' + (selected ? ' library-selected' : '')} data-library-row={key}
                  onContextMenu={event => {
                    if (directory) return;
                    event.preventDefault(); event.stopPropagation();
                    showMenu(item, event.currentTarget.querySelector('.library-item-name'), {x:event.clientX, y:event.clientY});
                  }}
                  onClick={event => {
                    if ((event.target as HTMLElement).closest('button,.library-check')) return;
                    void browse(item);
                  }}>
                  {!directory && <label className="library-check">{canPick(item) &&
                    <input type="checkbox" aria-label={'选择 ' + name} disabled={busy || Boolean(naming)} checked={selected}
                      onClick={event => {rangePick.current = event.shiftKey;}}
                      onChange={() => {choose(item,true,rangePick.current);rangePick.current = false;}}/>}</label>}
                  <button type="button" className="library-item-name" disabled={busy || Boolean(naming)}
                    title={item.relative || name} aria-label={name} onClick={() => void browse(item)}>
                    {spaceList ? <Users size={17}/> : directory && !local ? <BookOpen size={17}/> : isFolder ? <Folder size={17}/> : <FileText size={17}/>}
                    <span className="library-name"><span>{name.slice(0, ext)}</span>{ext < name.length && <span>{name.slice(ext)}</span>}</span>
                  </button>
                  {detailColumn && <span className="library-item-meta" title={meta}>{meta}</span>}
                  {!spaceList && <time className="library-date">{date(item.updated_at || item.update_time || item.modified_time)}</time>}
                  {directory ? <ChevronRight size={16} className="library-row-more" aria-hidden="true"/> : <button type="button" className="icon-button library-row-more" aria-haspopup="menu"
                    aria-expanded={Boolean(menu?.row && libraryItemKey(menu.row) === key)} aria-label={'操作 ' + name} title="更多操作" disabled={busy || Boolean(naming)} onClick={disclosure}><Ellipsis size={17}/></button>}
                </div>}
              </Fragment>;
            })}
            {naming && !naming.item && folderInsert < 0 && nameEditor}
            {!busy && !naming && !visible.length && <Empty title={searching || query.trim() ? '没有找到资料' : publicFiles ? '此文件夹为空' : spaceList ? '暂无' + labels.groups : '这里还没有资料'}
              detail={local && directory && !query.trim() ? '可新建项目或关联本机目录。' : ''}/>}
            {busy && !job && <p className="muted" role="status">正在处理…</p>}
            {!directory && more && <button className="secondary library-load-more" disabled={busy || Boolean(naming)} onClick={() => void children(collection, parents, page + 1, searchTerm)}>加载更多</button>}
          </div>
        </div>
      </>}
      {renderedMenu}
    </section>
  </div>;
}

function LibraryPreview({data}:{data:any}) {
  const kind=data.payload?.preview_kind;
  if(kind==='external')return <Empty icon={FileText} title="在本机应用中查看这份资料" detail="可以打开原文件，或将它添加到当前对话。"/>;
  if(kind==='pdf')return <iframe title="资料预览" src={data.payload.url+'#navpanes=0&view=FitH'}/>;
  if(kind==='image')return <img className="library-preview-image" alt="资料预览" src={data.payload.url}/>;
  if(kind==='html')return <iframe title="资料预览" sandbox="allow-scripts" src={data.payload.url}/>;
  if(data.html)return <iframe title="资料预览" sandbox="" referrerPolicy="no-referrer" srcDoc={'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: https:; style-src \'unsafe-inline\'">'+data.html}/>;
  const text=typeof data==='string'?data:data.text||data.content||JSON.stringify(data,null,2);
  return data.format==='text'?<pre>{text}</pre>:<article className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{typeof text==='string'?text:JSON.stringify(text,null,2)}</Markdown></article>;
}
