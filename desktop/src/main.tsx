import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';
import './management.css';
import './workspace.css';
import './composer.css';
import './plugins/plugins.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
