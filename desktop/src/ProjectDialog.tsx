import {useEffect, useRef, useState} from 'react';
import {Folder} from 'lucide-react';
import Dialog from './Dialog';
import type {ProjectRecord} from './types';

export default function ProjectDialog({root, project, onClose, onSave, confirm}: {
  root?: string; project?: ProjectRecord; onClose: () => void; onSave: (name: string, path: string) => Promise<void>;
  confirm: (title: string, detail: string, choices: string[]) => Promise<number>;
}) {
  const [name, setName] = useState(project?.name || '');
  const [path, setPath] = useState(project?.path || '');
  const [busy, setBusy] = useState<'folder' | 'save' | null>(null);
  const [error, setError] = useState('');
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {nameInput.current?.focus();}, []);
  const separator = root?.includes('\\') ? '\\' : '/';
  const nameValue = name.trim();
  const directory = path || (root && nameValue ? root + separator + nameValue : root);
  const dirty = project && (name !== project.name || path !== project.path);

  async function close() {
    if (busy) return;
    if (dirty && await confirm('放弃项目修改？', '项目名称或工作目录的修改尚未保存。', ['继续编辑', '放弃修改']) !== 1) return;
    onClose();
  }

  async function chooseFolder() {
    setBusy('folder');
    setError('');
    try {
      const selected = await window.cowork.call<string | null>('dialog.workspace', {default_path: path || undefined});
      if (selected) {
        setPath(selected);
        if (!project) setName(current => current.trim() ? current : selected.split(/[\\/]/).filter(Boolean).at(-1) || selected);
      }
    } catch (failure) {setError((failure as Error).message);}
    finally {setBusy(null);}
  }

  async function save() {
    if (!nameValue || busy) return;
    setBusy('save');
    setError('');
    try {
      await onSave(nameValue, path);
    } catch (failure) {setError((failure as Error).message); setBusy(null);}
  }

  return <Dialog title={project ? '编辑项目' : '新建项目'} className="project-dialog" onClose={() => void close()}>
    <form onSubmit={event => {event.preventDefault(); void save();}}>
      <label>项目名称<input ref={nameInput} required maxLength={60} value={name} readOnly={Boolean(busy)} placeholder="输入项目名称"
        onChange={event => {setName(event.target.value); setError('');}}/></label>
      <div className="project-directory">
        <span>{path ? '工作目录' : '默认工作目录'}</span>
        {project ? <div className="project-directory-row">
          <Folder size={16} aria-hidden="true"/><strong>{directory}</strong>
          <button className="secondary" type="button" disabled={Boolean(busy)} onClick={() => void chooseFolder()}>更换…</button>
        </div> : directory && <strong>{directory}</strong>}
      </div>
      {!project && <div className="button-row project-folder-actions">
        <button className="secondary" type="button" disabled={Boolean(busy)} onClick={() => void chooseFolder()}><Folder size={16}/>{path ? '更换文件夹…' : '关联本机文件夹…'}</button>
        {path && <button className="text-link" type="button" disabled={Boolean(busy)} onClick={() => {setPath(''); setError('');}}>使用默认目录</button>}
      </div>}
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="button-row project-dialog-actions">
        <button className="secondary" type="button" disabled={Boolean(busy)} onClick={() => void close()}>取消</button>
        <button className="primary" type="submit" disabled={Boolean(busy) || !nameValue}>{busy === 'save' ? '正在保存…' : '保存'}</button>
      </div>
    </form>
  </Dialog>;
}
