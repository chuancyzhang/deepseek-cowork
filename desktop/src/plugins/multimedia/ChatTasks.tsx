import {useState} from 'react';
import type {MediaTask} from '../../types';
import {MediaResult} from '../../MessageBubble';
import ImageAttachment,{isImageAttachment} from '../../ImageAttachment';
import './multimedia.css';

const labels: Record<string, string> = {image_generate: '图片生成', video_generate: '视频生成', audio_generate: '文字配音', image_understand: '图片理解', video_understand: '视频理解', audio_understand: '音频转写'};
const statuses: Record<string, string> = {needs_configuration: '等待配置模型', queued: '正在准备', submitting: '正在准备', running: '正在生成', downloading: '正在保存', completed: '已完成', paused: '等待已中断', unknown: '未确认接单', not_submitted: '未提交', download_failed: '保存未完成', failed: '未完成', cancelled: '已停止'};
const activeStatuses = new Set(['queued', 'submitting', 'running', 'downloading']);
type TaskProps = {cid: string; configured: string[]; openFile: (path: string) => void; configure: () => void};
type Operation = 'continue' | 'resume' | 'retry' | 'stop';

function TaskCard({cid, task, configured, openFile, configure}: TaskProps & {task: MediaTask}) {
  const [busy, setBusy] = useState<Operation | null>(null), [failure, setFailure] = useState('');
  const [playable, setPlayable] = useState(false);
  const video = task.capability === 'video_generate';
  const active = activeStatuses.has(task.status);
  const ready = video && task.status === 'completed' && Boolean(task.files?.length);
  const stopping = active && Boolean(task.stop_requested);
  const message = task.status === 'unknown' && !task.can_resume ? '需求已保留，未重复提交。' : task.message;
  const showMessage = !ready && (!active || stopping || Boolean(task.diagnostic));
  async function action(operation: Operation) {
    setBusy(operation); setFailure('');
    try {await window.cowork.call('media.' + operation, {id: task.id, conversation_id: cid, ...(operation === 'retry' ? {updated_at: task.updated_at} : {})});}
    catch (error) {setFailure((error as Error).message);}
    finally {setBusy(null);}
  }
  const button = (operation: Operation, label: string) => <button className="secondary" disabled={Boolean(busy) || stopping} onClick={() => void action(operation)}>{busy === operation ? '正在处理…' : label}</button>;
  const control = task.needs_configuration ? (configured.includes(task.capability) ? button('continue', '继续任务') : <button className="secondary" onClick={configure}>配置模型</button>)
    : task.can_retry ? button('retry', '重试生成')
    : task.can_resume ? button('resume', task.status === 'download_failed' ? '重试下载' : '继续等待')
    : active ? button('stop', '停止等待') : null;
  return <article>
    <div className="media-task-heading"><strong>{labels[task.capability] || '多媒体任务'}</strong><span role="status">{ready ? playable ? '可播放' : '已保存' : statuses[task.status] || '状态未记录'}{task.status === 'running' && task.progress != null ? ' · ' + task.progress + '%' : ''}</span></div>
    {showMessage && <p>{message}</p>}
    {control && <div className="button-row">{control}</div>}
    {failure && <p className="error-text" role="alert">{failure}</p>}
    {!active && task.diagnostic && <details className="media-task-diagnostic"><summary>查看原因</summary><p>{task.diagnostic.message}</p>{task.diagnostic.request_id && <p>请求编号：{task.diagnostic.request_id}</p>}</details>}
    {task.files?.map(path => {
      const name = path.split(/[\\/]/).pop() || path;
      if (isImageAttachment({path})) return <ImageAttachment key={path} cid={cid} file={{path, name}} onOpen={openFile}/>;
      if (/\.(mp4|webm|mov|mp3|wav|m4a|ogg|flac)$/i.test(path)) {
        const player = <MediaResult cid={cid} onFile={openFile} onPlayable={video ? setPlayable : undefined} file={{path, name, kind: /\.(mp4|webm|mov)$/i.test(path) ? 'video' : 'audio', url: '', label: '', references: [path]}}/>;
        return <div className={'message-media' + (video ? ' task-video' : '')} key={path}>{player}</div>;
      }
      return <button className="text-link" key={path} onClick={() => openFile(path)}>{name}</button>;
    })}
  </article>;
}

export default function ChatTasks({tasks, ...props}: TaskProps & {tasks: MediaTask[]}) {
  if (!tasks.length) return null;
  return <section className="chat-media-tasks" aria-label="多媒体任务与成果">{tasks.map(task => <TaskCard key={task.id} task={task} {...props}/>)}</section>;
}
