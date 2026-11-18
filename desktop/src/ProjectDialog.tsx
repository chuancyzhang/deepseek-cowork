import {useEffect, useRef, useState} from 'react';
import {Folder} from 'lucide-react';
import Dialog from './Dialog';

export default function ProjectDialog({root, onClose, onSaved}: {
  root?: string; onClose: () => void; onSaved: (path: string) => void;
}) {
  const [name, setName] = useState('');
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {nameInput.current?.focus();}, []);
  const separator = root?.includes('\\') ? '\\' : '/';
  const nameValue = name.trim();
  const directory = path || (root && nameValue ? root + separator + nameValue : root);

  async function chooseFolder() {
    setBusy(true);
    setError('');
    try {
      const selected = await window.cowork.call<string | null>('dialog.workspace');
      if (selected) {
        setPath(selected);
        setName(current => current.trim() ? current : selected.split(/[\\/]/).filter(Boolean).at(-1) || selected);
      }
    } catch (failure) {setError((failure as Error).message);}
    finally {setBusy(false);}
  }

  async function save() {
    if (!nameValue || busy) return;
    setBusy(true);
    setError('');
    try {
      const project = await window.cowork.call<{path: string}>('project.save', {name: nameValue, path: path || undefined});
      onSaved(project.path);
    } catch (failure) {setError((failure as Error).message); setBusy(false);}
  }

  return <Dialog title="新建项目" className="project-dialog" onClose={() => {if (!busy) onClose();}}>
    <form onSubmit={event => {event.preventDefault(); void save();}}>
      <label>项目名称<input ref={nameInput} required maxLength={60} value={name} readOnly={busy} placeholder="输入项目名称"
        onChange={event => {setName(event.target.value); setError('');}}/></label>
      <div className="project-directory">
        <span>{path ? '工作目录' : '默认工作目录'}</span>
        {directory && <strong>{directory}</strong>}
      </div>
      <div className="button-row project-folder-actions">
        <button className="secondary" type="button" disabled={busy} onClick={() => void chooseFolder()}><Folder size={16}/>{path ? '更换文件夹…' : '关联本机文件夹…'}</button>
        {path && <button className="text-link" type="button" disabled={busy} onClick={() => {setPath(''); setError('');}}>使用默认目录</button>}
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="button-row project-dialog-actions">
        <button className="secondary" type="button" disabled={busy} onClick={onClose}>取消</button>
        <button className="primary" type="submit" disabled={busy || !nameValue}>保存</button>
      </div>
    </form>
  </Dialog>;
}
