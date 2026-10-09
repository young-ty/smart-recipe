// 演示快照走虚拟模块：普通构建拿到的是空壳，演示构建与测试拿到真实数据。
// 映射在 vite.config.ts 的 resolve.alias 里，改那边记得同步这里。

declare module 'virtual:demo-fixtures' {
  const fixtures: unknown
  export default fixtures
}
