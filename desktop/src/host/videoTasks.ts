import type {MessageGroup} from '../runtime';
import type {MediaTask} from '../types';
import type {HostStore} from './store';

type Anchor = {groupId: string; runId: string; callId: string; order: number};
type VideoOwner = {taskId: string; path: string; groupId?: string};
type VideoLayout = {
  groups: Map<string, string[]>;
  unplaced: string[];
  files: Map<string, VideoOwner>;
  hasOtherTasks: boolean;
};

export function videoPathKey(path: string, workspace = '') {
  if (/^file:\/\//i.test(path)) {
    try {
      const url = new URL(path);
      path = (url.hostname ? '//' + url.hostname : '') + decodeURIComponent(url.pathname);
    } catch { return ''; }
  }
  path = path.replace(/^\/(?=[a-z]:[\\/])/i, '');
  if (workspace && !/^(?:[a-z]:[\\/]|[\\/])/i.test(path)) path = workspace + '/' + path;
  const windows = /^(?:[a-z]:[\\/]|\\\\|\/\/)/i.test(path);
  if (windows) path = path.replaceAll('\\', '/').toLowerCase();
  const root = path.match(/^(?:[a-z]:\/|\/\/[^/]+\/[^/]+\/?|\/)/i)?.[0] || '';
  const parts: string[] = [];
  for (const part of path.slice(root.length).split('/')) {
    if (part === '..' && parts.length && parts.at(-1) !== '..') parts.pop();
    else if (part && part !== '.' && (part !== '..' || !root)) parts.push(part);
  }
  return root + (root && !root.endsWith('/') && parts.length ? '/' : '') + parts.join('/');
}

export function videoTaskLayout(tasks: MediaTask[], groups: MessageGroup[], cid: string): VideoLayout {
  const videos = tasks.filter(task => task.conversation_id === cid && task.capability === 'video_generate');
  const layout: VideoLayout = {groups: new Map(), unplaced: [], files: new Map(),
    hasOtherTasks: tasks.some(task => task.conversation_id === cid && task.capability !== 'video_generate')};
  if (!videos.length) return layout;

  const calls = new Map<string, Anchor>();
  const results = new Map<string, Anchor>();
  const key = (run: string, call: string) => run + '\0' + call;
  for (const group of groups) {
    if (!group.assistant) continue;
    for (const message of group.messages) {
      const runId = message.meta?.turn_id || message.meta?.ui_turn_id || '';
      for (const call of message.tool_calls || []) {
        if ((call.function?.name || call.name) === 'media_create') {
          const id = key(runId, call.id);
          if (!calls.has(id)) calls.set(id, {groupId: group.id, runId, callId: call.id, order: calls.size});
        }
      }
      if (message.role !== 'tool' || !message.tool_call_id) continue;
      const id = key(runId, message.tool_call_id);
      if (message.meta?.tool_name === 'media_create' && !calls.has(id)) {
        calls.set(id, {groupId: group.id, runId, callId: message.tool_call_id, order: calls.size});
      }
      const anchor = calls.get(id);
      if (anchor && message.result_obj?.task_id) results.set(message.result_obj.task_id, anchor);
    }
  }
  const placements = videos.map(task => {
    const result = results.get(task.id);
    const anchor = (task.run_id && task.tool_call_id ? calls.get(key(task.run_id, task.tool_call_id)) : undefined)
      || (result && (!task.run_id || result.runId === task.run_id) && (!task.tool_call_id || result.callId === task.tool_call_id) ? result : undefined);
    return {task, anchor};
  }).sort((a, b) => (a.anchor?.order ?? Infinity) - (b.anchor?.order ?? Infinity));
  for (const {task, anchor} of placements) {
    if (anchor) {
      const ids = layout.groups.get(anchor.groupId) || [];
      ids.push(task.id);
      layout.groups.set(anchor.groupId, ids);
    } else layout.unplaced.push(task.id);
    for (const path of task.files || []) {
      const filename = videoPathKey(path);
      if (!layout.files.has(filename)) layout.files.set(filename, {taskId: task.id, path, groupId: anchor?.groupId});
    }
  }
  return layout;
}

// Progress updates subscribe at the card. The workbench needs only placement and file ownership.
export function readVideoTaskLayout(host: HostStore, cid: string, groups: MessageGroup[]) {
  const key = host.sessionKey('media_tasks', cid);
  let previous: MediaTask[] | undefined, signature: string | undefined, layout: VideoLayout;
  return () => {
    const tasks = host.get<MediaTask[]>(key, () => []);
    if (tasks !== previous) {
      previous = tasks;
      const next = JSON.stringify(tasks.map(task => task.capability === 'video_generate'
        ? [task.id, task.run_id, task.tool_call_id, task.files] : null));
      if (next !== signature) {
        signature = next;
        layout = videoTaskLayout(tasks, groups, cid);
      }
    }
    return layout;
  };
}
