import type {FileReference, TurnFile} from './types';
import {pathKey} from './projects.ts';

export type TurnFileRow = {path: string; name: string; record?: TurnFile};
export function turnFileRows(references: FileReference[], records: TurnFile[]): TurnFileRow[] {
  const rows = new Map<string, TurnFileRow>();
  for (const record of records) rows.set(pathKey(record.path), {path: record.path, name: record.name, record});
  for (const file of references) if (!rows.has(pathKey(file.path))) rows.set(pathKey(file.path), {path: file.path, name: file.name});
  return [...rows.values()];
}

export const historyReasons: Record<string, string> = {
  purged: '历史差异正文已按存储配额清理，文件记录与当时统计仍保留。',
  baseline_missing: '缺少可信的执行前内容，无法还原本轮差异。',
  file_limit: '文件超过 2 MiB，历史正文未完整保存。',
  turn_limit: '本轮历史正文超过 16 MiB，已保留文件记录。',
  total_limit: '历史正文配额暂被运行中任务占用，已保留文件记录。',
  read_failed: '采集时未能读取完整内容，无法还原本轮差异。',
  directory: '已记录目录操作，未逐个保存目录内文件的历史内容。',
  diff_limit: '文件行数较多，已保存内容，但未计算文本差异。',
  pending: '文件操作的记录未完成，无法确认本轮最终内容；请核对当前文件。',
  binary: '此文件不支持文本差异，可下载本轮保存的内容副本。',
};
