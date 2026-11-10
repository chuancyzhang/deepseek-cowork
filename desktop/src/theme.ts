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
