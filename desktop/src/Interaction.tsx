import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, LoaderCircle } from 'lucide-react';
import { useHostField } from './host/context';

const CUSTOM_OPTION = '__custom__';

type Option = {value: string; label: string; description?: string};
type Question = {id: string; question: string; options?: Option[]};
type Answer = {text?: string; selected_options?: string[]};
type Response = Answer & {approved?: boolean; answers?: Record<string, Answer | string>};
type Receipt = {resolved: boolean; reason?: string; response?: Response};
type Draft = {answers: Record<string, string>; text: string; selections: string[]; customQuestions?: string[]};
type InteractionProps = {
  request: {
    request_id: string; kind: string; message?: string; title?: string; options?: Option[];
    questions?: Question[]; allow_free_text?: boolean; metadata?: {purpose?: string};
  };
  outcome?: {status?: string; answered?: boolean; approved?: boolean; response?: Response; value?: Response};
  conversationId: string;
  active: boolean;
  done: (requestId: string) => void;
};

function optionLabel(value: string, options: Option[] = []) {
  return options.find(option => option.value === value)?.label || value;
}

function optionLabels(value: Answer | undefined, options?: Option[]) {
  return value?.selected_options?.map(option => optionLabel(option, options)).join('、') || '';
}

function answerText(answer: Answer | string | undefined, options?: Option[]) {
  if (typeof answer === 'string') return optionLabel(answer, options);
  return answer?.text || optionLabels(answer, options);
}

export default function Interaction({ request, outcome, conversationId, active, done }: InteractionProps) {
  const key = `cowork-questionnaire:${conversationId}:${request.request_id}`;
  const questionnaire = request.kind === 'questionnaire';
  const questions = request.questions || [];
  const single = questionnaire && questions.length === 1;
  const title = (single ? questions[0].question : request.title) || (request.kind === 'approval' ? '需要你的确认' : '补充一些信息');
  const recommend = request.metadata?.purpose !== 'grill_checkpoint';
  const questionList = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      const saved = localStorage.getItem(key);
      if (saved) return JSON.parse(saved);
    } catch { /* Keep the pending request usable if cache storage is unavailable. */ }
    const answers = questions.map(question => [question.id,
      (recommend && question.options?.find(option => option.value !== CUSTOM_OPTION)?.value) || '',
    ]);
    return {answers: Object.fromEntries(answers), text: '', selections: []};
  });
  const [expanded, setExpanded] = useState(questions[0]?.id || '');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const [receipt, setReceipt] = useHostField<Receipt | null>('interaction-receipt:' + conversationId + ':' + request.request_id, null);
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(draft)); }
    catch { setFailure('回答尚未写入本机缓存，请保留当前页面或复制内容。'); }
  }, [key, draft]);

  const rows = questions.map(question => {
    const options = (question.options || []).filter(option => option.value !== CUSTOM_OPTION);
    const value = draft.answers[question.id] || '';
    const customDraft = draft.customQuestions?.includes(question.id);
    const selected = customDraft ? undefined : options.find(option => option.value === value);
    const custom = !options.length || (!selected && (customDraft || Boolean(value)));
    // Older drafts stored the custom option's value before any text was entered.
    const text = !customDraft && value === CUSTOM_OPTION && question.options?.some(option => option.value === CUSTOM_OPTION) ? '' : value;
    return {question, options, selected, custom, text};
  });

  function updateAnswer(id: string, value: string, custom: boolean) {
    setDraft(previous => {
      const customQuestions = (previous.customQuestions || []).filter(questionId => questionId !== id);
      if (custom) customQuestions.push(id);
      return {...previous, customQuestions, answers: {...previous.answers, [id]: value}};
    });
    setFailure('');
  }

  async function respond(value: boolean | string | string[] | Record<string, Answer>) {
    setBusy(true);
    setFailure('');
    try {
      const result = await window.cowork.call<Receipt | {result: Receipt}>('interaction.respond', {
        conversation_id: conversationId, request_id: request.request_id, value,
      });
      const accepted = 'result' in result ? result.result : result;
      if (!accepted.resolved) throw new Error(accepted.reason === 'ended'
        ? '这项请求已经结束，当前回答已保留，可查看和复制。' : '回答尚未被接收，请检查后重新提交。');
      setReceipt(accepted);
      done(request.request_id);
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function submit() {
    if (!questionnaire) {
      void respond(request.kind === 'multi_choice' ? draft.selections : draft.text);
      return;
    }
    const missing = rows.findIndex(row => !row.selected && !row.text.trim());
    if (missing !== -1) {
      const row = rows[missing];
      setExpanded(row.question.id);
      setFailure(rows.length > 1 ? `请完成第 ${missing + 1} 项回答。`
        : row.options.length && !row.custom ? '请选择或填写回答。' : '请填写回答。');
      requestAnimationFrame(() => {
        const field = questionList.current?.querySelector<HTMLInputElement>('.custom-answer')
          || questionList.current?.querySelector<HTMLInputElement>('.question-block input[type=radio]');
        field?.focus();
      });
      return;
    }
    void respond(Object.fromEntries(rows.map(({question, selected, text}) => [question.id,
      selected ? {selected_options: [selected.value]} : {text: text.trim()},
    ])));
  }

  if (outcome || receipt || !active) {
    const answered = receipt?.response ?? (outcome?.answered ? outcome.response ?? outcome.value : undefined);
    const submitted = Boolean(outcome?.answered || receipt);
    const approved = answered?.approved ?? outcome?.approved;
    const waitingForResponse = submitted && !answered && !outcome;
    const count = Object.keys(answered?.answers ?? {}).length;
    let summary = '已提交回答';
    if (!submitted) summary = outcome?.status === 'timeout' ? '请求已超时 · 回答未提交' : '请求已结束 · 回答未提交';
    else if (questionnaire && count) summary = `已提交 ${count} 项回答`;
    else if (request.kind === 'approval' && approved !== undefined) summary = approved ? '已允许这次操作' : '已拒绝这次操作';

    return <details className="interaction-receipt">
      <summary>{submitted && <Check size={14}/>}<span>{summary}</span><ChevronDown size={13}/></summary>
      {request.message && <p>{request.message}</p>}
      {waitingForResponse ? <p>正在读取提交的回答…</p> : questionnaire ? rows.map(({question, selected, text}) => (
        <div className="receipt-answer" key={question.id}>
          <strong>{question.question}</strong>
          <p>{submitted ? answerText(answered?.answers?.[question.id], question.options) || '回答内容未记录' : selected?.label || text}</p>
        </div>
      )) : request.kind !== 'approval' && <p>{submitted
        ? optionLabels(answered, request.options) || optionLabel(answered?.text || '', request.options) || '回答内容未记录'
        : draft.text}</p>}
      {failure && !submitted && <p className="error-text">{failure}</p>}
    </details>;
  }

  const complete = rows.filter(row => row.selected || row.text.trim()).length;
  return <section className="interaction" aria-label={title}>
    <div className="interaction-title">{title}</div>
    {request.message && <p>{request.message}</p>}
    {request.kind === 'approval' ? <div className="button-row">
      <button disabled={busy} className="secondary" onClick={() => void respond(false)}>拒绝</button>
      <button disabled={busy} className="primary" onClick={() => void respond(true)}>允许这次操作</button>
    </div> : <>
      <div className="questionnaire-scroll" ref={questionList}>
        {questionnaire ? rows.map(({question, options, selected, custom, text}, index) => {
          const open = single || expanded === question.id;
          const name = `${request.request_id}:${question.id}`;
          return <section className="question-block" key={question.id}>
            {!single && <button className="question-heading" aria-expanded={open} onClick={() => setExpanded(open ? '' : question.id)}>
              <span>{index + 1}. {question.question}{!open && <small>{selected?.label || text.trim() || '尚未填写'}</small>}</span><ChevronDown size={14}/>
            </button>}
            {open && <fieldset disabled={busy}>
              <legend className="sr-only">{question.question}</legend>
              {options.map((option, optionIndex) => <label className="choice" key={option.value}>
                <input type="radio" name={name} checked={selected === option} onChange={() => updateAnswer(question.id, option.value, false)}/>
                <span>{option.label}{recommend && optionIndex === 0 && !/推荐|recommended/i.test(option.label) ? '（推荐）' : ''}
                  {option.description && <small>{option.description}</small>}</span>
              </label>)}
              <div className="question-custom">
                {!!options.length && <label className="choice">
                  <input type="radio" name={name} checked={custom} onChange={() => updateAnswer(question.id, '', true)}/>
                  <span>自定义</span>
                </label>}
                {custom && <input type="text" className="custom-answer" aria-label={options.length ? `自定义回答 · ${question.question}` : question.question}
                  placeholder="输入回答" value={text} onChange={event => updateAnswer(question.id, event.target.value, true)}/>}
              </div>
            </fieldset>}
          </section>;
        }) : <>
          <div className="choice-options">{request.options?.map(option => <label className="choice" key={option.value}>
            <input type={request.kind === 'multi_choice' ? 'checkbox' : 'radio'} name={request.request_id}
              checked={request.kind === 'multi_choice' ? draft.selections.includes(option.value) : draft.text === option.value}
              onChange={() => setDraft(request.kind === 'multi_choice'
                ? {...draft, selections: draft.selections.includes(option.value) ? draft.selections.filter(item => item !== option.value) : [...draft.selections, option.value]}
                : {...draft, text: option.value})}/>
            <span>{option.label}{option.description && <small>{option.description}</small>}</span>
          </label>)}</div>
          {(request.allow_free_text || request.kind === 'text') && <textarea aria-label="输入回答" rows={3} value={draft.text} onChange={event => setDraft({...draft, text: event.target.value})}/>}
        </>}
      </div>
      <div className="questionnaire-footer">
        {rows.length > 1 && <span>已填写 {complete} / {rows.length} 项</span>}
        <button disabled={busy} className="primary" onClick={submit}>
          {busy && <LoaderCircle size={14} className="spin"/>}{busy ? '正在提交…' : '提交回答'}
        </button>
      </div>
    </>}
    {failure && <p role="alert" className="error-text">{failure}</p>}
  </section>;
}
