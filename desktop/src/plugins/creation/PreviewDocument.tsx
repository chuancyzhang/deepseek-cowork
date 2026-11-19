import type {Root} from 'react-dom/client';
import type {Plugin} from '../../types';
import {themeStyle} from '../../theme';
import {CandidateView} from './WorkbenchPreview';

export function installCreationPreview(root: Root) {
  Object.assign(window, {renderCreationPreview(plugin: Plugin, theme: unknown, mode: string) {
    document.documentElement.dataset.theme = mode;
    const surfaces = plugin.contributes.filter(extension => extension.kind !== 'menu');
    const extensions = surfaces.length ? surfaces : plugin.contributes.slice(0,1);
    root.render(<div className="creation-probe" data-preview-hash={plugin.hash} style={themeStyle(theme)}>
      {extensions.map((extension, index) => <div className={'creation-probe-surface' + (index ? ' creation-probe-hidden' : '')}
        key={extension.id}>
        <CandidateView plugin={plugin} extension={extension} theme={theme} mode={mode}/>
      </div>)}
    </div>);
  }});
}
