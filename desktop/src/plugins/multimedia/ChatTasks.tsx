import {useState} from 'react';
import type {MediaTask,FileReference} from '../../types';
import {MediaResult} from '../../MessageBubble';
import ImageAttachment,{isImageAttachment} from '../../ImageAttachment';
import {useHostField} from '../../host/context';
import './multimedia.css';

const labels:Record<string,string>={image_generate:'图片生成',video_generate:'视频生成',audio_generate:'文字配音',image_understand:'图片理解',video_understand:'视频理解',audio_understand:'音频转写'};
const statuses:Record<string,string>={needs_configuration:'等待配置模型',queued:'等待提交',submitting:'正在提交',running:'正在生成',downloading:'正在下载',completed:'已完成',paused:'已停止等待',unknown:'提交结果待确认',download_failed:'下载未完成',failed:'未完成',cancelled:'已停止'};
export default function ChatTasks({cid,tasks,configured,openFile,configure}:{cid:string;tasks:MediaTask[];configured:string[];openFile:(path:string)=>void;configure:()=>void}) {
  const [busy,setBusy]=useState(''),[failure,setFailure]=useState('');
  const [remoteIds,setRemoteIds]=useHostField<Record<string,string>>('media:remote-ids:'+cid,{});
  async function action(task:MediaTask,operation:string){setBusy(task.id);setFailure('');try{await window.cowork.call('media.'+operation,{id:task.id,conversation_id:cid,...(remoteIds[task.id]?{remote_id:remoteIds[task.id]}:{})});}catch(error){setFailure((error as Error).message);}finally{setBusy('');}}
  if(!tasks.length)return null;
  return <section className="chat-media-tasks" aria-label="多媒体任务与成果">{tasks.map(task=><article key={task.id}>
    <div className="media-task-heading"><strong>{labels[task.capability]||'多媒体任务'}</strong><span role="status">{statuses[task.status]||task.status}{task.status==='running'&&task.progress!=null?' · '+task.progress+'%':''}</span></div>
    <small>{[task.model,task.service_name].filter(Boolean).join(' · ')}</small><p>{task.message}</p>
    <div className="button-row">{task.needs_configuration&&(configured.includes(task.capability)?<button className="primary" disabled={busy===task.id} onClick={()=>void action(task,'continue')}>继续任务</button>:<button className="secondary" onClick={configure}>配置模型</button>)}{task.can_resume&&<button className="secondary" disabled={busy===task.id} onClick={()=>void action(task,'resume')}>{task.status==='download_failed'?'重试下载':'恢复查询或下载'}</button>}{['queued','submitting','running','downloading'].includes(task.status)&&<button className="secondary" disabled={busy===task.id} onClick={()=>void action(task,'stop')}>停止等待</button>}{task.status==='failed'&&<button className="secondary" onClick={configure}>检查多媒体配置</button>}</div>
    {task.can_attach_id&&<details><summary>补充远端编号</summary><p>先在服务后台核对提交结果。Agnes 填写 video_id，其他服务填写任务 id。</p><div className="input-action"><input aria-label="远端任务编号" value={remoteIds[task.id]||''} onChange={event=>setRemoteIds({...remoteIds,[task.id]:event.target.value})}/><button className="secondary" disabled={!remoteIds[task.id]||busy===task.id} onClick={()=>void action(task,'resume')}>恢复查询</button></div></details>}
    {task.files?.map(path=>{const name=path.split(/[\\/]/).pop()||path;
      if(isImageAttachment({path}))return <ImageAttachment key={path} cid={cid} file={{path,name}} onOpen={openFile}/>;
      if(/\.(mp4|webm|mov|mp3|wav|m4a|ogg|flac)$/i.test(path))return <div className="message-media" key={path}><MediaResult cid={cid} onFile={openFile} file={{path,name,kind:/\.(mp4|webm|mov)$/i.test(path)?'video':'audio',references:[path]} as FileReference}/></div>;
      return <button className="text-link" key={path} onClick={()=>openFile(path)}>{name}</button>;
    })}
  </article>)}{failure&&<p className="error-text" role="alert">{failure}</p>}</section>;
}
