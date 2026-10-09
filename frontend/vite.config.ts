import { cpSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

/**
 * `vite build --mode demo` 才生效：把 demo/dishes 的实拍图复制进构建产物。
 *
 * 演示站（GitHub Pages）没有后端，访客能拿到的只有静态文件，示例图必须跟着产物走。
 * 图片的真身在 demo/dishes 里（同时是测试 fixture 与原型页的输入），
 * 这里复制而不是再存一份到 public/，免得同一个文件两处维护、改了一处另一处还是旧的。
 */
function demoDishPhotos(): Plugin {
  let outDir = ''
  return {
    name: 'demo-dish-photos',
    apply: 'build',
    configResolved(config) {
      if (config.mode !== 'demo') {
        return
      }
      outDir = resolve(config.root, config.build.outDir)
    },
    closeBundle() {
      if (!outDir) {
        return
      }
      cpSync(fileURLToPath(new URL('../demo/dishes', import.meta.url)), `${outDir}/demo/dishes`, {
        recursive: true,
      })
      // GitHub Pages 默认拿 Jekyll 过一遍，下划线开头的文件会被丢掉
      writeFileSync(`${outDir}/.nojekyll`, '')
    },
  }
}

// 开发期把 /api 代理到本地后端，前端代码里只写相对路径，免去 CORS 与环境变量。
// 常规构建不发布 demo/ 图片（页面上的示例图入口已撤下，演示图只作测试 fixture 与
// prototype/dish.html 的输入）；只有演示构建（--mode demo）才会把图复制进产物。
export default defineConfig(({ mode }) => ({
  plugins: [react(), demoDishPhotos()],
  resolve: {
    alias: {
      // 演示快照只在演示构建与测试里进包：常规构建换成空壳，
      // 免得给用不到它的线上用户多发约 9 kB（gzip）。src/demo/index.ts 只认这个虚拟 id。
      'virtual:demo-fixtures': fileURLToPath(
        new URL(
          mode === 'demo' || mode === 'test'
            ? './src/demo/fixtures.json'
            : './src/demo/fixtures.stub.json',
          import.meta.url,
        ),
      ),
    },
  },
  // 相对基址：构建产物要能直接放在 GitHub Pages 的子路径（/仓库名/）下打开
  base: './',
  server: {
    port: 5173,
    // 监听所有网卡：手机和电脑连同一个热点时，用 Vite 打印的 Network 地址就能打开
    host: true,
    // 固定端口：菜谱记忆存在 localStorage 里，而 localStorage 按 origin（含端口）隔离，
    // 端口一漂移（5173 被占用时 vite 会自动改成 5174）记忆就被当成空的了。
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  // 预览构建产物时同样对外监听，方便用手机验收打包后的版本
  preview: {
    port: 4173,
    host: true,
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    // 测试不注入样式，但 layout.test.ts 需要用 ?raw 读到样式表原文来校验断点契约
    css: true,
    // 覆盖率报告（NFR-06）：产物落在 .codex-build/，数字写进 docs/测试报告.md
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary'],
      reportsDirectory: '../.codex-build/coverage-frontend',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx', 'src/**/*.d.ts'],
      // NFR-06 的验收线：任何一项掉到 80% 以下就让 test:coverage 失败
      thresholds: { lines: 80, statements: 80, branches: 80, functions: 80 },
    },
  },
}))
