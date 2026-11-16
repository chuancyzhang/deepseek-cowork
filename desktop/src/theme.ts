import type { CSSProperties } from 'react';
export function themeStyle(theme: any): CSSProperties {
  if (!theme) return {};
  const t = theme.tokens || {};
  const map: Record<string, string> = { '--bg': 'bg_main', '--sidebar': 'bg_sidebar', '--text': 'text_primary', '--muted': 'text_secondary',
    '--line': 'border_subtle', '--soft': 'bg_secondary', '--hover': 'bg_hover', '--primary': 'primary', '--on-primary': 'text_inverse', '--error': 'error_text',
    '--chat-bg': 'bg_chat', '--chat-text': 'chat_text', '--composer-bg': 'composer_bg', '--composer-text': 'composer_text', '--link': 'chat_link' };
  const style: Record<string, string> = {};
  for (const [css, token] of Object.entries(map)) if (t[token]) style[css] = t[token];
  style['--font-family'] = JSON.stringify(theme.font_family || 'Microsoft YaHei UI') + ', sans-serif';
  style['--mono-font'] = JSON.stringify(theme.mono_font_family || 'Consolas') + ', monospace';
  style['--font-scale'] = String(theme.font_scale || 1);
  style['--radius-scale'] = String(theme.radius_scale || 1);
  return style as CSSProperties;
}

// This exact mapping is also used by the standalone preview harness. Keep the
// fragment's tokens independent from parent CSS inheritance (sandboxed iframe).
export function visualizationThemeCss(styles: Pick<CSSStyleDeclaration, 'getPropertyValue'>, dark: boolean): string {
  const value = (name: string) => styles.getPropertyValue(name).trim();
  const mappings: Record<string, string> = {
    background: '--chat-bg', foreground: '--chat-text', 'muted-foreground': '--muted',
    border: '--line', muted: '--soft', accent: '--hover', 'accent-foreground': '--text',
    primary: '--primary', 'primary-foreground': '--on-primary', destructive: '--error',
  };
  const tokens = Object.entries(mappings).map(([target, source]) => {
    const color = value(source) || value(target === 'background' ? '--bg' : '--text');
    return `--${target}:${color};`;
  }).join('');
  // Derive category colors from the current theme's link hue; no fixed light
  // or dark palette. Relative colors resolve to actual canvas-ready colors.
  const link = value('--link') || value('--primary');
  const series = Array.from({length: 6}, (_, index) =>
    `--viz-series-${index + 1}:oklch(from ${link} ${dark ? '.78' : '.48'} min(c, .16) calc(h + ${index * 60}));`).join('');
  const font = value('--font-family') || 'sans-serif';
  return `:root{color-scheme:${dark ? 'dark' : 'light'};${tokens}${series}--font-size-base:calc(14px * ${value('--font-scale') || '1'});font-family:${font};font-size:var(--font-size-base);color:var(--foreground);background:var(--background)}
body{margin:12px;overflow-wrap:anywhere}*{box-sizing:border-box}button,input,select,textarea{font:inherit;color:var(--foreground);background:var(--background);border:1px solid var(--border)}button{cursor:pointer}button:disabled{cursor:default;opacity:.6}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--primary);outline-offset:2px}svg,canvas{max-width:100%}[hidden]{display:none!important}`;
}
