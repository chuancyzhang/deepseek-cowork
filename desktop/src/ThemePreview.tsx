import { useState } from 'react';
import { Check, X } from 'lucide-react';
import Dialog from './Dialog';
import { themeStyle } from './theme';

export function ThemeSample({ theme }: { theme: any }) {
  return <div className="theme-live-preview" style={themeStyle(theme)}><aside><span/><span/><span/></aside><section><h3>把想法变成成果</h3><div className="preview-user">帮我整理今天的资料</div><p>已经按主题整理好了。接下来可以查看要点，或继续完善文稿。</p><div className="preview-document">一份清晰的工作文稿</div><div className="preview-composer">继续写下你的想法…<span>↑</span></div></section></div>;
}

export default function ThemePreview({ candidate, close, changed }: {candidate: any; close: () => void; changed: () => Promise<void>}) {
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('');
  const [name, setName] = useState(candidate.preview.name);
  async function commit(method: string) {
    setBusy(true); setFailure('');
    try {await window.cowork.call(method, {id: candidate.preview.preview_id, revision: candidate.preview.revision, name}); await changed(); close();}
    catch (error) {setFailure((error as Error).message);} finally {setBusy(false);}
  }
  return <Dialog title="主题预览" className="theme-preview-dialog" onClose={() => {if (!busy) close();}}>
    <p>预览仅作用于下方示例。保存并应用后才会更改工作台。</p>
    <ThemeSample theme={candidate.resolved}/>
    <label className="theme-preview-name">主题名称<input value={name} disabled={busy} onChange={event => setName(event.target.value)}/></label>
    {failure && <p className="inline-error" role="alert">{failure}</p>}
    <div className="button-row"><button className="secondary" disabled={busy} onClick={close}>稍后再看</button><button className="secondary" disabled={busy} onClick={() => void commit('themes.discard_preview')}><X size={15}/>弃用候选</button><button className="primary" disabled={busy || !name.trim()} onClick={() => void commit('themes.commit_preview')}><Check size={15}/>{busy ? '正在保存…' : '保存并应用'}</button></div>
  </Dialog>;
}
