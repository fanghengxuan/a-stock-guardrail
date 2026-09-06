/** 工作台：输入标的 → 流式备忘录逐段点亮 → done 权威替换。 */
import { useState } from "react"
import type { FormEvent } from "react"
import { useReportStreamCtx } from "../ReportStreamProvider"
import { PHASE_LABEL, TOTAL_SECTIONS } from "../lib/constants"
import { MemoView } from "../memoView"
import type { DashState } from "../lib/dashReducer"

const PHASES = ["fetch", "drilldown", "write", "validate", "saved"] as const

function StreamBar({ state }: { state: DashState }) {
  if (state.status === "idle") return null
  const isThinking = state.status === "streaming"
    && state.progress.mdBytes === 0
    && state.progress.reasoningBytes > 0
  const curPhaseIdx = state.phase ? PHASES.indexOf(state.phase as typeof PHASES[number]) : -1
  const doneSegs = state.status === "done"
    ? TOTAL_SECTIONS
    : Math.max(0, Math.min(state.progress.sections, TOTAL_SECTIONS))
  // 正在写的当前节 = 第 doneSegs 格（0 基），最后一格写完前保持 live 呼吸
  const writingSeg = (state.status === "streaming" || state.status === "connecting")
    && doneSegs < TOTAL_SECTIONS ? doneSegs : -1
  // 标的展示：优先权威 meta（写完后），次取 SSE resolved 的代码，最后回退用户输入
  const metaN = state.blocks.meta?.name
  const metaC = state.blocks.meta?.code
  const tgtName = metaN || state.resolved?.name?.trim() || "…"
  const rawCode = (state.resolved?.code || metaC || "").trim()
  const tgtCode = rawCode || (/^\d{6}$/.test(state.resolved?.name ?? "") ? state.resolved!.name! : "")
  return (
    <div className={`streambar ${state.status === "done" ? "done" : ""}`}>
      <div className="streambar-head">
        <span className="tgt">
          <span className="tgt-name">{tgtName}</span>
          {tgtCode && (
            <span className="tgt-code">
              {tgtCode}{state.blocks.meta?.exchange ? `.${state.blocks.meta.exchange}` : ""}
            </span>
          )}
          <span className="tgt-state">{state.status === "done" ? "分析完成" : state.status === "error" ? "分析中断" : "正在生成投资备忘录…"}</span>
        </span>
      </div>
      <div className="streambar-in">
        <span className="phase" key={state.phase ?? "init"}>
          {state.status === "done" ? "✓ 备忘录生成完成"
            : state.status === "error" ? "✗ 未完成"
              : isThinking ? "▍模型推理中"
                : `▍${PHASE_LABEL[state.phase ?? ""] ?? "建立连接"}`}
          {isThinking && (
            <span className="thinking-dots"><span /><span /><span /></span>
          )}
        </span>
        <span className="track seg-track" title="报告每写完一节点亮一格（总数随报告章节结构动态变化）">
          {Array.from({ length: TOTAL_SECTIONS }, (_, i) => (
            <span key={i} className={[
              "seg",
              i < doneSegs ? "on" : "",
              i === writingSeg ? "live" : "",
            ].join(" ")} />
          ))}
        </span>
        {state.status === "done" ? (
          <span className="stat sec-pop" key="done">全节完成</span>
        ) : (
          <span className="stat sec-pop" key={doneSegs}>{doneSegs} 节已点亮</span>
        )}
        {isThinking ? (
          <span className="stat thinking">推理{(state.progress.reasoningBytes / 1024).toFixed(1)}KB</span>
        ) : (
          <span className="stat">{(state.progress.mdBytes / 1024).toFixed(0)}KB</span>
        )}
        <span className="stat">{state.progress.elapsed}s</span>
      </div>
      {(state.status === "streaming" || state.status === "connecting") && (
        <div className="phase-rail">
          {PHASES.map((p, i) => {
            const done = curPhaseIdx >= 0 && i < curPhaseIdx
            const active = curPhaseIdx === i
            return (
              <span key={p} className={`pr-item ${done ? "done" : ""} ${active ? "active" : ""}`}>
                <span className="pr-dot" />
                {PHASE_LABEL[p] ?? p}
              </span>
            )
          })}
        </div>
      )}
      {(state.status === "streaming" || state.status === "connecting") && (
        <div className="thought">
          <span className="txt">
            {isThinking ? "正在分析策略规则、匹配数据底稿…" : state.thought?.tail || "…"}
          </span>
          <span className="cursor" />
        </div>
      )}
    </div>
  )
}

export default function WorkspacePage() {
  const { state, start, reset, deepThink, setDeepThink } = useReportStreamCtx()
  const [input, setInput] = useState("")
  const [refresh, setRefresh] = useState(false)
  const busy = state.status !== "idle" && state.status !== "done" && state.status !== "error"

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const q = input.trim()
    if (!q || busy) return
    start(q, refresh, deepThink)
  }

  return (
    <>
      <div className="wrap cmd">
        <form className="cmd-form" onSubmit={submit}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入股票代码或名称，如 600036 / 招商银行"
            aria-label="分析标的"
            autoFocus
            disabled={busy}
          />
          <button type="submit" disabled={!input.trim() || busy}>
            {busy ? "分析中…" : "生成备忘录"}
          </button>
        </form>
        <p className="cmd-tip">
          <label>
            <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} disabled={busy} />
            {" "}强制刷新（清缓存重新取数）
          </label>
          {" "}
          <label title="开启后模型先深度推理再写报告，更缜密，但正文要等推理完才逐节流出（前 3–5 分钟显示“模型推理中”）">
            <input type="checkbox" checked={deepThink} onChange={(e) => setDeepThink(e.target.checked)} disabled={busy} />
            {" "}深度思考
          </label>
          {" "}· 全流程约 2–5 分钟，报告每写完一节，备忘录点亮对应段落
          {state.status === "done" && state.resolved && (
            <button onClick={reset} style={{ border: 0, background: "none", color: "var(--seal)", cursor: "pointer", textDecoration: "underline" }}>清空重来</button>
          )}
        </p>
        {state.error && <p className="err-banner">{state.error}</p>}
      </div>
      <StreamBar state={state} />
      <MemoView
        blocks={state.blocks}
        streaming={state.streaming}
      />
    </>
  )
}
