import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';
import './management.css';
import './workspace.css';
import './composer.css';
import './plugins/plugins.css';
const root = createRoot(document.getElementById('root')!);
if (location.hash === '#creation-preview') {
  const {installCreationPreview} = await import('./plugins/creation/PreviewDocument');
  installCreationPreview(root);
} else {
  root.render(<React.StrictMode><App /></React.StrictMode>);
}
