import { useEffect, useRef, useState } from "react";
import type { Job } from "./api";

interface Props {
  job: Job | null;
  frameNumber: number | null;
  aspect: string;
  preview?: string;
  totalSteps?: number;
  /** 不是剛送出的任務，只是拿最近的作品擺著 */
  recent?: boolean;
  onCancel: (job: Job) => void;
  onOpen: (job: Job) => void;
}

/** 正在「顯影」的那一格：排隊、取樣預覽由模糊轉清晰、完成後定影。 */
export function DevelopFrame({ job, frameNumber, aspect, preview, totalSteps, recent, onCancel, onOpen }: Props) {
  const [finalLoaded, setFinalLoaded] = useState(false);
  const finalRef = useRef<HTMLImageElement>(null);
  const finalSrc = job?.status === "succeeded" && job.images[0] ? `/outputs/${job.images[0]}` : null;
  // 圖片已在快取時 onLoad 可能比 React 綁定事件還早觸發，所以另外檢查 complete
  useEffect(() => setFinalLoaded(!!finalRef.current?.complete && !!finalRef.current.naturalWidth), [finalSrc]);

  const progress = job?.progress ?? 0;
  const running = job?.status === "running";
  const step = totalSteps ? Math.min(totalSteps, Math.max(1, Math.round(progress * totalSteps))) : null;

  return (
    <section className="develop" aria-labelledby="develop-title">
      <h2 id="develop-title" className="visually-hidden">目前的生成</h2>
      <div className={`film film-single state-${job?.status ?? "empty"}`}>
        <div className="edge" aria-hidden>
          <span>{frameNumber ?? ""}</span>
          <span>{frameNumber ? `${frameNumber}A` : ""}</span>
        </div>

        <div className="frame" style={{ aspectRatio: aspect }}>
          {preview && !finalLoaded && (
            <img
              className="layer preview"
              src={preview}
              alt=""
              style={{ filter: `blur(${(1 - progress) * 10 + 3}px) saturate(${0.4 + progress * 0.6})` }}
            />
          )}
          {finalSrc && (
            <button className="layer final-button" onClick={() => job && onOpen(job)} aria-label="放大檢視這張圖">
              <img
                ref={finalRef}
                className={finalLoaded ? "final developed" : "final"}
                src={finalSrc}
                alt={String(job?.params.prompt ?? "")}
                onLoad={() => setFinalLoaded(true)}
              />
            </button>
          )}

          {!finalSrc && (
            <div className="frame-note" aria-live="polite">
              {!job && <p>寫下畫面描述，按「開始生成」，圖片會在這裡慢慢顯影。</p>}
              {job?.status === "queued" && (
                <p>{job.queue_position ? `排隊中，前面還有 ${job.queue_position} 張` : "下一張就輪到你"}</p>
              )}
              {running && !preview && <p>{progress > 0 ? "開始顯影" : "正在載入模型，第一次會比較久"}</p>}
              {job?.status === "failed" && <p className="note-error">{job.error ?? "生成失敗"}</p>}
              {job?.status === "cancelled" && <p>這張已取消</p>}
            </div>
          )}

          {running && (
            <div
              className="develop-progress"
              role="progressbar"
              aria-label="生成進度"
              aria-valuenow={Math.round(progress * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div style={{ transform: `scaleX(${progress})` }} />
            </div>
          )}
        </div>

        <div className="edge" aria-hidden>
          <span />
          <span />
        </div>
      </div>

      <div className="develop-caption">
        <p className="develop-status">
          {job?.status === "running" && (step ? `顯影中，第 ${step}／${totalSteps} 步` : "顯影中")}
          {job?.status === "queued" && "等待中"}
          {job?.status === "succeeded" && (recent ? "最近完成的一張，點圖片可以放大" : "完成，點圖片可以放大")}
          {job?.status === "failed" && "這張沒有成功"}
          {job?.status === "cancelled" && "已取消"}
        </p>
        {job && (job.status === "queued" || job.status === "running") && (
          <button className="quiet" onClick={() => onCancel(job)}>
            取消這張
          </button>
        )}
      </div>
    </section>
  );
}
