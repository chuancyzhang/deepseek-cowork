import { RefreshCw } from 'lucide-react';

export default function DraftRecovery({ pending, busy = false, recover }: {
  pending: boolean; busy?: boolean; recover: (retry: boolean) => void;
}) {
  if (!pending) return null;
  return <div className="draft-recovery" role="status"><RefreshCw size={15}/><span>上一条消息的接收结果待确认，当前输入已保留。</span>
    <button disabled={busy} onClick={() => recover(false)}>{busy?'正在核对…':'核对状态'}</button><button disabled={busy} onClick={() => recover(true)}>重试原消息</button></div>;
}
