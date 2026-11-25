import {useCallback, useEffect, useRef, useState, useSyncExternalStore} from 'react';
import type {MediaTask} from '../../types';
import {MediaResult} from '../../MessageBubble';
import ImageAttachments,{isImageAttachment} from '../../ImageAttachment';
import {useHost, useHostField} from '../../host/context';
import {receiveMediaTasks} from '../../host/conversations';
import {videoPathKey} from '../../host/videoTasks';
import './multimedia.css';

const labels: Record<string, string> = {image_generate: '图片生成', video_generate: '视频生成', audio_generate: '文字配音', image_understand: '图片理解', video_understand: '视频理解', audio_understand: '音频转写'};
const statuses: Record<string, string> = {needs_configuration: '等待配置模型', queued: '正在准备', submitting: '正在准备', running: '正在生成', downloading: '正在保存', completed: '已完成', paused: '等待已中断', unknown: '未确认接单', not_submitted: '未提交', download_failed: '保存未完成', failed: '未完成', cancelled: '已停止', rate_limited: '视频服务限流'};
const activeStatuses = new Set(['queued', 'submitting', 'running', 'downloading']);
type TaskProps = {cid: string; configured: string[]; openFile: (path: string) => void; configure: () => void};
type Operation = 'continue' | 'resume' | 'retry' | 'stop' | 'refresh';
const emptyTasks = () => [] as MediaTask[];

function TaskCard({cid, task, configured, openFile, configure, visible = true, files = task.files}: TaskProps & {
  task: MediaTask; visible?: boolean; files?: string[];
}) {
  const host = useHost();
  const article = useRef<HTMLElement>(null);
  const [busy, setBusy] = useState<Operation | null>(null), [failure, setFailure] = useState('');
  const [openPath, setOpenPath] = useState(''), [missing, setMissing] = useState('');
  const [playable, setPlayable] = useState(false);
  const [requestedPath, setRequestedPath] = useHostField(host.sessionKey('videoPreview:' + task.id, cid), '');
  const video = task.capability === 'video_generate';
  const active = activeStatuses.has(task.status);
  const ready = video && task.status === 'completed' && Boolean(files?.length);
  const stopping = active && Boolean(task.stop_requested);
  const message = video && task.status === 'unknown' && !task.can_refresh && !task.can_resume
    ? '视频要求已保留。请到服务端核对是否已接单。' : task.message;
  const showMessage = !ready && (!active || stopping || Boolean(task.diagnostic));
  const images = (files || []).filter(path => isImageAttachment({path})).map(path => ({path, name:path.split(/[\\/]/).pop() || path}));
  const onMissing = useCallback(() => {setMissing(openPath); setPlayable(false);}, [openPath]);
  useEffect(() => {
    if (!visible || !ready) {
      setOpenPath(''); setMissing(''); setPlayable(false);
    }
  }, [visible, ready]);
  useEffect(() => {
    if (!visible || !requestedPath) return;
    setRequestedPath('');
    if (ready && files.includes(requestedPath)) {
      setOpenPath(requestedPath);
      setMissing('');
    }
    article.current?.scrollIntoView({block: 'nearest'});
    article.current?.focus({preventScroll: true});
  }, [visible, requestedPath, ready, files, setRequestedPath]);
  async function action(operation: Operation) {
    setBusy(operation); setFailure('');
    try {
      const result = await window.cowork.call<MediaTask>('media.' + operation, {
        id: task.id, conversation_id: cid,
        ...(operation === 'retry' ? (task.revision != null ? {expected_revision: task.revision} : {updated_at: task.updated_at}) : {}),
        ...(operation === 'resume' && missing ? {missing_file: missing} : {}),
      });
      if (result) receiveMediaTasks(host, cid, [result]);
      if (operation === 'resume' && missing) {setMissing(''); setOpenPath('');}
    }
    catch (error) {setFailure((error as Error).message);}
    finally {setBusy(null);}
  }
  const button = (operation: Operation, label: string) => <button className="secondary" disabled={Boolean(busy) || stopping} onClick={() => void action(operation)}>{busy === operation ? '正在处理…' : label}</button>;
  const control = missing ? task.can_redownload ? button('resume', '重试下载') : null
    : task.needs_configuration ? (configured.includes(task.capability) ? button('continue', '继续任务') : <button className="secondary" onClick={configure}>配置模型</button>)
    : task.can_retry ? button('retry', '重试生成')
    : task.can_refresh ? button('refresh', '刷新状态')
    : task.can_resume ? button('resume', task.status === 'download_failed' ? '重试下载' : task.phase === 'generated' ? '下载成片' : '继续等待')
    : active ? button('stop', '停止等待') : null;
  let status = statuses[task.status] || '状态未记录';
  if (stopping) status = '正在停止等待';
  else if (ready) status = missing ? '本地成片找不到' : playable ? '可播放' : '已保存';
  else if (task.phase === 'generated' && task.status === 'paused') status = '已生成，待下载';
  else if (task.status === 'running' && task.phase === 'accepted') status = '服务已接单';
  else if (task.status === 'running' && task.phase === 'queued') status = '服务排队中';
  else if (task.status === 'running' && task.progress != null) status += ' · ' + task.progress + '%';
  return <article ref={article} tabIndex={video ? -1 : undefined} data-video-task={video ? task.id : undefined}>
    <div className="media-task-heading"><strong>{labels[task.capability] || '多媒体任务'}</strong><span role="status">{status}</span></div>
    {showMessage && <p>{message}</p>}
    {missing && !task.can_redownload && <p>可将原成片放回工作目录后重新打开。</p>}
    {control && <div className="button-row">{control}</div>}
    {failure && <p className="error-text" role="alert">{failure}</p>}
    {!active && task.diagnostic && <details className="media-task-diagnostic"><summary>查看原因</summary><p>{task.diagnostic.message}</p>{task.diagnostic.request_id && <p>请求编号：{task.diagnostic.request_id}</p>}</details>}
    {images.length > 0 && <ImageAttachments cid={cid} files={images} onOpen={openFile}/>}
    {(!video || ready) && files?.filter(path => !isImageAttachment({path})).map(path => {
      const name = path.split(/[\\/]/).pop() || path;
      const file = {path, name, kind: /\.(mp4|webm|mov)$/i.test(path) ? 'video' as const : 'audio' as const, url: '', label: '', references: [path]};
      if (video) {
        const expanded = visible && openPath === path;
        return <div key={path}>
          <div className="video-result-row"><span>{name}</span><button type="button" className="secondary" aria-expanded={expanded}
            onClick={() => {setOpenPath(expanded ? '' : path); setMissing(''); setPlayable(false);}}>{expanded ? '收起成片' : '展开成片'}</button></div>
          {expanded && missing !== path && <div className="message-media task-video"><MediaResult cid={cid} onFile={openFile} onPlayable={setPlayable}
            taskId={task.id} onMissing={onMissing} file={file}/></div>}
        </div>;
      }
      if (/\.(mp4|webm|mov|mp3|wav|m4a|ogg|flac)$/i.test(path)) {
        return <div className="message-media" key={path}><MediaResult cid={cid} onFile={openFile} file={file}/></div>;
      }
      return <button className="text-link" key={path} onClick={() => openFile(path)}>{name}</button>;
    })}
  </article>;
}

export default function ChatTasks(props: TaskProps) {
  const host = useHost();
  const [all] = useHostField(host.sessionKey('media_tasks', props.cid), emptyTasks);
  const tasks = all.filter(task => task.capability !== 'video_generate');
  if (!tasks.length) return null;
  return <section className="chat-media-tasks" aria-label="多媒体任务与成果">{tasks.map(task => <TaskCard key={task.id} task={task} {...props}/>)}</section>;
}

type VideoProps = TaskProps & {videoFiles: ReadonlyMap<string,{taskId: string; path: string}>; visible?: boolean};

function VideoTask({id, videoFiles, ...props}: VideoProps & {id: string}) {
  const host = useHost();
  const key = host.sessionKey('media_tasks', props.cid);
  const task = useSyncExternalStore(
    useCallback(listener => host.subscribe(key, listener), [host, key]),
    useCallback(() => host.get(key, emptyTasks).find(item => item.id === id), [host, key, id]),
  );
  if (!task) return null;
  const files = (task.files || []).filter(path => {
    const owner = videoFiles.get(videoPathKey(path));
    return owner?.taskId === task.id && owner.path === path;
  });
  return <TaskCard task={task} files={files} {...props}/>;
}

export function VideoTasks({ids, heading, ...props}: VideoProps & {ids: string[]; heading?: boolean}) {
  return <section className="chat-media-tasks chat-video-tasks" aria-label="视频任务">
    {heading && <h3>视频任务</h3>}
    {ids.map(id => <VideoTask key={id} id={id} {...props}/>)}
  </section>;
}
