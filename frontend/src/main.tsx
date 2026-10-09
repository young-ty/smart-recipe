import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
import './styles/tokens.css'
import './styles/app.css'

const container = document.getElementById('root')
if (!container) {
  throw new Error('未找到 #root 挂载点')
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// PWA：只在生产构建里注册 service worker。开发期注册会把 HMR 的中间产物缓存下来，
// 改了代码却看到旧页面，排查成本远大于收益。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`)
  })
}
