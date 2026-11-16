import { defineConfig } from 'vite';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
export default defineConfig({ base: './', build: { target: 'es2022', outDir: 'dist', emptyOutDir: true }, plugins: [{
  name:'code-editor-licenses',
  generateBundle(_options,bundle) {
    const roots=new Set<string>();
    for(const chunk of Object.values(bundle))if(chunk.type==='chunk')for(const id of Object.keys(chunk.modules)){
      const match=id.replaceAll('\\','/').match(/^(.*\/node_modules\/(?:@codemirror\/[^/]+|@lezer\/[^/]+|@marijn\/find-cluster-break|crelt|style-mod|w3c-keyname))\//);
      if(match)roots.add(match[1]);
    }
    const sections=[...roots].sort().map(root=>{
      const pkg=JSON.parse(readFileSync(join(root,'package.json'),'utf8'));
      return `${pkg.name} ${pkg.version}\n\n${readFileSync(join(root,'LICENSE'),'utf8')}`;
    });
    this.emitFile({type:'asset',fileName:'code-editor-licenses.txt',source:sections.join('\n\n---\n\n')});
  },
}] });
