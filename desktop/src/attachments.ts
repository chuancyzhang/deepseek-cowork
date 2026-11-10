import type { Attachment } from './types';
import { defaultUrlTransform } from 'react-markdown';

export function markdownUrl(url: string): string {
  return /^(?:[a-z]:[\\/]|file:\/\/)/i.test(url) ? url : defaultUrlTransform(url);
}

export function markdownFilePath(url: string): string {
  let value = url.replace(/#(?:L\d+|page=.*)$/, '');
  if (/^file:\/\//i.test(value)) {
    const file = new URL(value);
    value = (file.hostname && file.hostname !== 'localhost' ? '//' + file.hostname : '') + file.pathname;
  }
  // A literal percent is valid in a file name; decode only well-formed escapes.
  try { value = decodeURIComponent(value); } catch { /* Already a literal path. */ }
  return value.replace(/^\/([a-z]:[\\/])/i, '$1');
}

export async function receiveAttachments(conversationId: string, files: File[]): Promise<Attachment[]> {
  const added: Attachment[] = [];
  for (const file of files) {
    const path = window.cowork.pathForFile(file);
    if (path) added.push(...await window.cowork.call<Attachment[]>('workspace.attach', {conversation_id: conversationId, paths: [path]}));
    else {
      if (file.size > 40 * 1024 * 1024) throw new Error('粘贴附件超过 40 MiB，请先保存到文件，再通过“添加文件”选择。');
      const base64 = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('无法读取附件，请重新选择。')); reader.readAsDataURL(file); });
      added.push(await window.cowork.call<Attachment>('workspace.attach_data', {conversation_id: conversationId, name: file.name || '粘贴图片.png', base64}));
    }
  }
  return added;
}
