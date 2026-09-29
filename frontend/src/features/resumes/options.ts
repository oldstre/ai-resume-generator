/**
 * 简历表单的下拉选项。
 *
 * 这些数组给 PillSelect 组件用：用户在新建简历时选语气、段数、内容密度。
 * value 是传给后端的实际值，label 是显示给用户的中文。
 *
 * 取值范围跟后端 schemas/resumes.py 的 MIN_SECTION_COUNT / MAX_SECTION_COUNT 对齐。
 */

import type { ContentDensity, Tone } from './types'

/** 后端 schemas/resumes.py 里的范围常量。 */
export const SECTION_COUNT_RANGE = { min: 3, max: 12 } as const

/** 语气风格——对应后端 Tone Literal。 */
export const TONE_OPTIONS: Array<{ value: Tone; label: string; description?: string }> = [
  { value: 'professional', label: '正式', description: '用词严谨、书面化，适合大厂校招' },
  { value: 'plain', label: '亲切', description: '口语化、易读，适合初创团队' },
  { value: 'punchy', label: '自信', description: '动词开头、有冲击力，适合销售岗' },
]

/** 内容密度——对应后端 ContentDensity Literal。 */
export const DENSITY_OPTIONS: Array<{
  value: ContentDensity
  label: string
  description?: string
}> = [
  { value: 'concise', label: '简洁', description: '一句话说清，适合一页纸简历' },
  { value: 'medium', label: '适中', description: '结论 + 关键细节，默认推荐' },
  { value: 'detailed', label: '详尽', description: '含项目背景、数据、成果' },
]

/** 段落数选项：3-12 之间的整数。 */
export const SECTION_COUNT_OPTIONS = Array.from(
  { length: SECTION_COUNT_RANGE.max - SECTION_COUNT_RANGE.min + 1 },
  (_, index) => {
    const count = SECTION_COUNT_RANGE.min + index
    return { value: count, label: `${count} 段` }
  },
)
