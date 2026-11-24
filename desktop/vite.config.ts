import { defineConfig } from 'vite';
import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
export default defineConfig({ base: './', build: { target: 'es2022', outDir: 'dist', emptyOutDir: true }, plugins: [{
  name:'pdf-reader-resources',
  configureServer(server) {
    server.middlewares.use((request,response,next)=>{
      const match=request.url?.split('?')[0].match(/^\/pdfjs\/(cmaps|standard_fonts|wasm|iccs)\/([A-Za-z0-9_.-]+)$/);
      if(!match)return next();
      try{response.setHeader('Content-Type',match[2].endsWith('.wasm')?'application/wasm':'application/octet-stream');response.end(readFileSync(join(import.meta.dirname,'node_modules/pdfjs-dist',match[1],match[2])));}
      catch{response.statusCode=404;response.end();}
    });
  },
  generateBundle() {
    const root=join(import.meta.dirname,'node_modules/pdfjs-dist');
    for(const folder of ['cmaps','standard_fonts','wasm','iccs'])for(const name of readdirSync(join(root,folder))){
      this.emitFile({type:'asset',fileName:`pdfjs/${folder}/${name}`,source:readFileSync(join(root,folder,name))});
    }
    this.emitFile({type:'asset',fileName:'pdfjs/LICENSE',source:readFileSync(join(root,'LICENSE'))});
  },
}, {
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
