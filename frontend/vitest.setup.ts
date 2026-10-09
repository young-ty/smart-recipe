import '@testing-library/jest-dom/vitest'

// jsdom 未实现 Object URL API，补测试替身，避免预览图逻辑在测试中报错
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:mock-preview'
  URL.revokeObjectURL = () => undefined
}