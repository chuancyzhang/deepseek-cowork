import type {ReactNode} from 'react';

export type ModelChoice = {key: string; name: string; detail?: string; disabled?: boolean};

export default function ModelSelection({choices, selected, onChange, onAdd, disabled, cancel}: {
  choices: ModelChoice[];
  selected: string[];
  onChange: (keys: string[]) => void;
  onAdd: (keys: string[]) => void;
  disabled: boolean;
  cancel: ReactNode;
}) {
  const checked = new Set(selected);
  const eligible = choices.filter(choice => !choice.disabled).map(choice => choice.key);
  const picked = eligible.filter(key => checked.has(key));
  const all = eligible.length > 0 && picked.length === eligible.length;
  return <section className="model-catalog" aria-label="可添加模型">
    <div className="section-heading"><h3>可添加模型</h3>
      {eligible.length > 0 && <button type="button" className="secondary" disabled={disabled} onClick={() => onChange(all ? [] : eligible)}>{all ? '取消全选' : '全选'}</button>}
    </div>
    {choices.length ? <div className="model-selection">{choices.map(choice =>
      <label className="model-choice" key={choice.key}>
        <input type="checkbox" checked={checked.has(choice.key) && !choice.disabled} disabled={disabled || choice.disabled}
          onChange={event => onChange(event.target.checked ? [...picked, choice.key] : picked.filter(key => key !== choice.key))}/>
        <span>{choice.name}{choice.detail && <small>{choice.detail}</small>}</span>
      </label>
    )}</div> : <p className="muted" role="status">没有可添加的新模型</p>}
    <div className="button-row model-catalog-actions">{cancel}
      <button type="button" className="primary" disabled={disabled || !picked.length} onClick={() => onAdd(picked)}>
        <span aria-live="polite">{picked.length ? `添加 ${picked.length} 个模型` : '添加模型'}</span>
      </button>
    </div>
  </section>;
}
