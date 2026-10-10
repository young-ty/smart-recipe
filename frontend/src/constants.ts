// 与后端 core/config.py 对齐的前端常量（设计方案 4.4、5.3.1）

export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024

/** 超过这个大小就先在浏览器里压一遍再上传（手机实拍基本都在 3 MB 以上） */
export const COMPRESS_THRESHOLD_BYTES = 1.5 * 1024 * 1024

/** 压缩后长边的上限：模型输入只有 288×288，1600 已经远远够用 */
export const COMPRESS_MAX_EDGE = 1600

/** JPEG 编码质量。0.82 在手机实拍上肉眼看不出差别，体积能掉一大截 */
export const COMPRESS_QUALITY = 0.82

/** 超过这个大小连解码都可能把浏览器拖住，不试压缩，直接劝退 */
export const UPLOAD_HARD_LIMIT_BYTES = 25 * 1024 * 1024

export const ALLOWED_MIME_TYPES = ['image/jpeg', 'image/png', 'image/webp']

export const EMPTY_HINT_IDLE = '识别出菜品后，这里显示菜谱与做法'

export const EMPTY_HINT_NO_CANDIDATE = '识别后这里显示候选菜品与置信度'

export const EMPTY_HINT_MEMORY = '取过菜谱后这里会记住做过的菜：下次识别到同一道菜直接复用，不再请求大模型'

/** 类别清单未交付前，名称一律带「待确认」标记（设计方案 7.2） */
export const PROVISIONAL_TAG = '名称待确认'

export const SOURCE_LABEL: Record<string, string> = {
  library: '库内菜谱',
  generated: '大模型生成',
  unavailable: '暂无数据',
}
