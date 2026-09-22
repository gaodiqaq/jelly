import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import ErrorBoundary from './ErrorBoundary'
import './styles.css'
import './studio.css'
import './workbench.css'

createRoot(document.getElementById('root')).render(
  <ErrorBoundary><App /></ErrorBoundary>,
)
