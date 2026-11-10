import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { FileHost } from '../electron/files.cjs';
import { skillInMode, skillMatches } from '../src/skills.ts';
import { markdownUrl, markdownFilePath } from '../src/attachments.ts';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import Markdown from 'react-markdown';

test('Markdown file links retain Windows paths while script URLs stay blocked', () => {
  const url='D:/项目/工作目录/说明.md';
  const rendered=renderToStaticMarkup(createElement(Markdown,{urlTransform:markdownUrl},`[文件](${url})`));
  assert.ok(rendered.includes('href="D:/'),rendered);
  assert.equal(markdownFilePath('file:///D:/%E9%A1%B9%E7%9B%AE/%E8%AF%B4%E6%98%8E.md#L12'),url.replace('/工作目录',''));
  assert.equal(markdownFilePath('/D:/资料/说明.md'), 'D:/资料/说明.md');
  assert.equal(markdownFilePath('file://server/share/report.md'), '//server/share/report.md');
  assert.equal(markdownFilePath('进度100%.md'), '进度100%.md');
  assert.equal(markdownUrl('javascript:alert(1)'), '');
  assert.equal(markdownUrl('https://example.org'), 'https://example.org');
});

test('preview URLs only expose the authorized document and bounded page assets', async () => {
  const root = new URL('../../.tmp/desktop/files/', import.meta.url);
  await fs.mkdir(root, {recursive:true});
  const folder = await fs.mkdtemp(new URL('case-', root));
  const html = path.join(folder, '中文.html'), asset = path.join(folder, 'style.css');
  try {
    await fs.writeFile(html, '<p>saved</p>'); await fs.writeFile(asset, 'body{}');
    await fs.writeFile(path.join(folder, 'private.txt'), 'private');
    const host = new FileHost();
    const file = host.register({path:html,payload:{kind:'html'}});
    const fetch = async url => new Response(await fs.readFile(new URL(url)));
    assert.equal(await (await host.response(new Request(file.payload.url),fetch)).text(), '<p>saved</p>');
    const css = new URL('style.css',file.payload.url);
    assert.equal(await (await host.response(new Request(css),fetch)).text(), 'body{}');
    assert.equal((await host.response(new Request(new URL('private.txt', file.payload.url)),fetch)).status,403);
    assert.equal((await host.response(new Request('cowork-local://file/unknown/file'),fetch)).status,404);
    const draft = host.draft(file.payload.url, '<p>draft</p>');
    assert.equal(await (await host.response(new Request(draft),fetch)).text(),'<p>draft</p>');
    assert.equal(await fs.readFile(html,'utf8'),'<p>saved</p>');
  } finally {await fs.rm(folder,{recursive:true,force:true});}
});

test('skill ownership and task search preserve the existing categories', () => {
  const builtin={source_kind:'core_builtin',name:'file',editable:false};
  const official={source_type:'bundled_plugin',name:'docs',presentation:{category:'docs_knowledge',examples:['读 PDF','整理文档']}};
  const user={name:'my-skill',source_kind:'user',editable:true};
  assert.equal(skillInMode(builtin,'library'),false);
  assert.equal(skillInMode(builtin,'builtin'),true);
  assert.equal(skillInMode(official,'library'),true);
  assert.equal(skillInMode(official,'mine'),false);
  assert.equal(skillInMode(user,'mine'),true);
  assert.equal(skillMatches(official,'PDF'),true);
});
