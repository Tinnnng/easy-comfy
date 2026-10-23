import type { Job } from "./api";
import { tiltHandlers, useConfirm } from "./fx/effects";

interface Props {
  jobs: Job[];
  numberOf: (job: Job) => number;
  /** 目前在放大檢視的任務；它的縮圖要讓出 view-transition-name */
  openId: string | null;
  onOpen: (job: Job) => void;
  onDelete: (job: Job) => void;
  onPrioritize: (job: Job) => void;
}

const STATUS: Partial<Record<Job["status"], string>> = {
  queued: "排隊中",
  running: "生成中",
  failed: "失敗",
  cancelled: "已取消",
};

const tilt = tiltHandlers();

export function Gallery({ jobs, numberOf, openId, onOpen, onDelete, onPrioritize }: Props) {
  return (
    <ol className="gallery">
      {jobs.map((job) => {
        const n = numberOf(job);
        const prompt = job.sources?.prompt ?? (typeof job.params.prompt === "string" ? job.params.prompt : "");
        const image = job.images[0];
        const seconds =
          job.started_at && job.finished_at
            ? (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000
            : null;
        return (
          <li key={job.id} className={`tile state-${job.status}`}>
            <button
              className="tile-card"
              data-job={job.id}
              onClick={() => onOpen(job)}
              aria-label={`第 ${n} 張：${prompt}`}
              {...tilt}
            >
              <span className="tile-media">
                {image ? (
                  <img
                    src={`/outputs/${image}`}
                    alt=""
                    loading="lazy"
                    decoding="async"
                    style={{ viewTransitionName: openId === job.id ? "none" : `shot-${job.id}` }}
                  />
                ) : (
                  <span className="tile-empty">{STATUS[job.status]}</span>
                )}
                <span className="tile-glare" aria-hidden />
              </span>
              <span className="tile-meta" aria-hidden>
                <span>#{String(n).padStart(3, "0")}</span>
                {seconds !== null && job.status === "succeeded" && <span>{seconds.toFixed(1)}s</span>}
              </span>
            </button>
            {job.status !== "running" && <TileDelete n={n} onConfirm={() => onDelete(job)} />}
            {job.status === "queued" && !!job.queue_position && (
              <button type="button" className="tile-priority" onClick={() => onPrioritize(job)}>
                移到最前面
              </button>
            )}
            <p className="tile-prompt" title={prompt}>
              {prompt}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

/** 卡片右上角的刪除鈕：第一次按變成確認狀態，再按一次才刪。 */
function TileDelete({ n, onConfirm }: { n: number; onConfirm: () => void }) {
  const { armed, trigger, reset } = useConfirm(onConfirm);
  return (
    <button
      type="button"
      className={armed ? "tile-delete is-armed" : "tile-delete"}
      onClick={trigger}
      onBlur={reset}
      aria-label={armed ? `再按一次確認刪除第 ${n} 張` : `刪除第 ${n} 張`}
    >
      <TrashIcon />
      {armed && <span>確認刪除</span>}
    </button>
  );
}

export function TrashIcon() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden>
      <path
        d="M4 6h12M8 6V4.5A1.5 1.5 0 0 1 9.5 3h1A1.5 1.5 0 0 1 12 4.5V6m2 0-.6 9.1a2 2 0 0 1-2 1.9H8.6a2 2 0 0 1-2-1.9L6 6m2.5 3v5m3-5v5"
        fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      />
    </svg>
  );
}
