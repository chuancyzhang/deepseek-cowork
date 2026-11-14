import { AlertCircle, RefreshCw } from 'lucide-react';

export default function DraftRecovery({ conflict, pending, resolve, recover }: {
  conflict: boolean; pending: boolean; resolve: (keepLocal: boolean) => void; recover: (retry: boolean) => void;
}) {
  if (pending) return <div className="draft-recovery" role="status"><RefreshCw size={15}/><span>上一条消息的接收结果待确认，当前输入已保留。</span>
    <button onClick={() => recover(false)}>核对状态</button><button onClick={() => recover(true)}>重试原消息</button></div>;
  if (!conflict) return null;
  return <div className="draft-recovery" role="status"><AlertCircle size={15}/><span>恢复的草稿与已保存版本不同。</span>
    <button onClick={() => resolve(true)}>保留当前输入</button><button onClick={() => resolve(false)}>载入已保存版本</button></div>;
}
