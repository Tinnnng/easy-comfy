import { useEffect, useState } from "react";
import type { Job } from "./api";
import { DenoiseCanvas } from "./fx/DenoiseCanvas";
import { useTween } from "./fx/effects";

interface Props {
  job: Job | null;
  aspect: [number, number];
  preview?: string;
  totalSteps: number;
  /** 不是剛送出的任務，只是拿最近的作品擺著 */
  recent: boolean;
  onCancel: (job: Job) => void;
  onOpen: (job: Job) => void;
}

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

function useElapsed(job: Job | null) {
  const [now, setNow] = useState(() => Date.now());
  const running = job?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(timer);
  }, [running]);
  if (!job?.started_at) return null;
  const end = job.finished_at ? new Date(job.finished_at).getTime() : now;
  return Math.max(0, (end - new Date(job.started_at).getTime()) / 1000);
}

/** 顯影台：WebGL 去雜訊畫面 + 取樣遙測。 */
export function DevelopPanel({ job, aspect, preview, totalSteps, recent, onCancel, onOpen }: Props) {
  const status = job?.status ?? "empty";
  const progress = job?.progress ?? 0;
  const finalSrc = job?.status === "succeeded" && job.images[0] ? `/outputs/${job.images[0]}` : null;
  const step = status === "succeeded" ? totalSteps : Math.round(progress * totalSteps);
  const elapsed = useElapsed(job);
  const shownProgress = useTween(status === "succeeded" ? 1 : progress, job?.id);

  let src: string | null = null;
  let noise = 1;
  if (finalSrc) {
    src = finalSrc;
    noise = 0;
  } else if (status === "running" && preview) {
    src = preview;
    noise = Math.max(0.1, 0.9 - progress * 0.85);
  } else if (status === "failed" || status === "cancelled") {
    src = preview ?? null;
    noise = 0.85;
  }

  const prompt = String(job?.params.prompt ?? "");
  const translatedFrom = job?.sources?.prompt;
  const message =
    status === "empty" ? "寫下畫面描述並按「開始生成」，雜訊會在這裡收斂成圖片。"
    : status === "queued" ? (job?.queue_position ? `排隊中，前面還有 ${job.queue_position} 張` : "下一張就輪到你")
    : status === "running" && !preview ? (progress > 0 ? "開始去雜訊" : "正在把模型載入顯示卡，第一次會比較久")
    : status === "failed" ? job?.error ?? "生成失敗"
    : status === "cancelled" ? "這張已取消"
    : null;

  return (
    <section className={`develop state-${status}`} aria-labelledby="develop-title">
      <h2 id="develop-title" className="visually-hidden">目前的生成</h2>

      <div className="viewport" style={{ aspectRatio: `${aspect[0]} / ${aspect[1]}` }}>
        <DenoiseCanvas
          className="viewport-canvas"
          src={src}
          noise={noise}
          revealKey={finalSrc ?? undefined}
          alt={finalSrc ? prompt : "正在生成的畫面"}
        />
        {finalSrc && job && (
          <button className="viewport-open" onClick={() => onOpen(job)} aria-label="放大檢視這張圖" />
        )}
        {message && (
          <p className={status === "failed" ? "viewport-note is-error" : "viewport-note"} aria-live="polite">
            {message}
          </p>
        )}
        <span className="corner tl" aria-hidden />
        <span className="corner tr" aria-hidden />
        <span className="corner bl" aria-hidden />
        <span className="corner br" aria-hidden />
        {status === "running" && <span className="scanline" aria-hidden />}
      </div>

      <div className="telemetry" aria-live="off">
        <div className="readout">
          <span className="readout-label">步數</span>
          <span className="readout-value">
            {pad(step)}
            <small>/{pad(totalSteps)}</small>
          </span>
        </div>
        <div className="readout">
          <span className="readout-label">進度</span>
          <span className="readout-value">
            {Math.round(shownProgress * 100)}
            <small>%</small>
          </span>
        </div>
        <div className="readout">
          <span className="readout-label">經過</span>
          <span className="readout-value">
            {elapsed === null ? "--" : elapsed.toFixed(1)}
            <small>s</small>
          </span>
        </div>
        <SigmaSteps steps={totalSteps} done={step} active={status === "running"} />
      </div>

      <div className="develop-foot">
        <p className="develop-status">
          {status === "running" && (step > 0 ? `去雜訊中，第 ${step} 步` : "載入模型與編碼描述中")}
          {status === "queued" && "等待顯示卡"}
          {status === "succeeded" && (recent ? "最近完成的一張，點畫面可以放大" : "完成，點畫面可以放大")}
          {status === "failed" && "這張沒有成功"}
          {status === "cancelled" && "已取消"}
        </p>
        {job && (status === "queued" || status === "running") && (
          <button className="ghost" onClick={() => onCancel(job)}>
            取消這張
          </button>
        )}
      </div>

      {translatedFrom && !recent && (
        <div className="translation">
          <span className="translation-label">已自動翻成英文送給模型</span>
          <p lang="en">{prompt}</p>
        </div>
      )}
    </section>
  );
}

/** 每一步剩下的雜訊強度 σ。Flux schnell 的排程是線性遞減，所以畫成一階一階往下的柱子。 */
function SigmaSteps({ steps, done, active }: { steps: number; done: number; active: boolean }) {
  return (
    <div className="sigma" role="img" aria-label={`雜訊強度排程，已完成 ${done} / ${steps} 步`}>
      <span className="readout-label">σ 雜訊強度</span>
      <div className="sigma-bars">
        {Array.from({ length: steps }, (_, i) => (
          <span
            key={i}
            className={i < done ? "bar is-done" : i === done && active ? "bar is-active" : "bar"}
            style={{ height: `${((steps - i) / steps) * 100}%` }}
          />
        ))}
      </div>
    </div>
  );
}
