import { useCallback } from 'react'

import { CandidateList } from '../components/CandidateList'
import { PhotoRecognizeCard } from '../components/PhotoRecognizeCard'
import { RecipeMemoryCard } from '../components/RecipeMemoryCard'
import { RecipePanel } from '../components/RecipePanel'
import { DEMO_MODE, DEMO_SAMPLES, demoNotice, demoSampleById, fetchDemoImageFile } from '../demo'
import { useRecipeMemory } from '../hooks/useRecipeMemory'
import { useRecipePage } from '../hooks/useRecipePage'

// 健康检查返回前的占位；返回后即替换为真实模型版本与类别数
const FALLBACK_CAPTION = '模型: 检测中…'

export function RecipeRecommendPage() {
  // 菜谱记忆（FR-15）：做过的菜记在本机；识别到做过的菜直接复用这里的菜谱，
  // 不再请求后端（库外菜也就不再消耗大模型额度）
  const memory = useRecipeMemory()
  const {
    state,
    selectImage,
    recognize,
    selectCandidate,
    submitManualDish,
    showMemory,
    reset,
  } = useRecipePage({
    onResolved: memory.remember,
    findRemembered: memory.find,
  })

  const model = state.model
  const caption = model
    ? `模型: ${model.version} · ${model.class_count} 类 · ${
        model.labels_available ? '类别清单已交付' : '类别名称待确认'
      }${model.degraded ? ' · 当前为模拟结果' : ''}`
    : FALLBACK_CAPTION
  // 演示站不跑推理，徽标就不能写「真实模型推理」：数字是真的，但它是固化下来的
  const badge = DEMO_MODE
    ? '预置模型输出'
    : model == null
      ? '模型状态检测中'
      : model.degraded
        ? '模拟 CV 模型推理'
        : '真实模型推理'

  const image = state.image
  const statusText = image ? `已选择本地图片 · ${image.file.name}` : null
  const provisionalName =
    state.candidates.find((item) => item.index === state.selectedIndex)?.provisional_name ?? false

  const demoFile = DEMO_MODE ? (image?.file ?? null) : null
  const activeSampleId = demoFile
    ? (DEMO_SAMPLES.find((sample) => sample.fileName === demoFile.name)?.id ?? null)
    : null
  const selectSample = useCallback(
    async (id: string) => {
      const sample = demoSampleById(id)
      if (sample) {
        selectImage(await fetchDemoImageFile(sample))
      }
    },
    [selectImage],
  )

  return (
    <>
      <header className="topbar">
        <h1 className="app-title">基于深度学习的智能菜谱识别</h1>
        <p className="app-sub">拍照记录一道好菜 → 认出菜名 → 一键扒谱</p>
        {DEMO_MODE ? (
          <p className="demo-banner">
            静态演示站：不跑模型、不联网。候选与置信度是 <code>models/dish.onnx</code> 的真实输出，
            菜谱是同一条链路真实返回后固化的快照（库外那些由大模型生成后落库）；自己传的照片只演示交互流程。
          </p>
        ) : null}
      </header>
      <div className="app">
        <div className="col col--photo">
          <PhotoRecognizeCard
            previewUrl={image?.previewUrl ?? null}
            status={state.status}
            error={state.error}
            caption={caption}
            badge={badge}
            statusText={statusText}
            onSelectFile={selectImage}
            onRecognize={() => {
              void recognize()
            }}
            onReset={reset}
            samples={DEMO_MODE ? DEMO_SAMPLES : null}
            activeSampleId={activeSampleId}
            notice={demoFile ? demoNotice(demoFile) : null}
            onSelectSample={(id) => {
              void selectSample(id)
            }}
          />
        </div>
        <div className="col col--candidates">
          <CandidateList
            candidates={state.candidates}
            selectedIndex={state.selectedIndex}
            manualDish={state.manualDish}
            showManual={image !== null}
            onSelect={selectCandidate}
            onSubmitManual={submitManualDish}
          />
        </div>
        <div className="col col--recipe">
          <RecipePanel resolve={state.resolve} provisionalName={provisionalName} />
          <RecipeMemoryCard
            entries={memory.entries}
            onShow={showMemory}
            onClear={memory.clear}
          />
        </div>
      </div>
    </>
  )
}
