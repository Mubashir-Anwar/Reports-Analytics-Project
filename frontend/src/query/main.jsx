import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import QueryApp from './QueryApp.jsx';
import '../shared/theme.css';
import '../portal/portal.css';
import './query.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <QueryApp />
  </StrictMode>,
);
