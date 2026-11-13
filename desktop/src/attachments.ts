import type { Attachment, FileReference } from './types';
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

/** Link validated file references in prose/inline code, never fenced commands. */
export function remarkFileReferences(files: FileReference[] = []) {
  const references = files.flatMap(file => file.references.map(text => ({text, url:file.url})))
    .filter(item => item.text).sort((a,b) => b.text.length - a.text.length);
  return (tree: any) => {
    const visit = (node: any) => {
      if (!node.children || ['link','linkReference','code','html'].includes(node.type)) return;
      node.children = node.children.flatMap((child: any) => {
        if (!['text','inlineCode'].includes(child.type)) {visit(child); return [child];}
        const result = []; let remaining = child.value;
        while (remaining) {
          const match = references.map(item => ({...item,index:remaining.indexOf(item.text)})).filter(item => item.index >= 0)
            .sort((a,b) => a.index - b.index || b.text.length - a.text.length)[0];
          if (!match) {result.push({...child,value:remaining}); break;}
          if (match.index) result.push({...child,value:remaining.slice(0,match.index)});
          result.push({type:'link',url:match.url,children:[{type:child.type,value:match.text}]});
          remaining = remaining.slice(match.index + match.text.length);
        }
        return result.length ? result : [child];
      });
    };
    visit(tree);
  };
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
