import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import AdminPage from './AdminPage'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* Tiny path switch — the app has no router and /admin is its only other page.
        nginx falls back to index.html, so /admin serves this same bundle. */}
    {window.location.pathname.replace(/\/+$/, '') === '/admin' ? <AdminPage /> : <App />}
  </React.StrictMode>
)
