import type { Job } from "./api";
import { tiltHandlers } from "./fx/effects";

interface Props {
  jobs: Job[];
  numberOf: (job: Job) => number;
  /** 目前在放大檢視的任務；它的縮圖要讓出 view-transition-name */
  openId: string | null;
  onOpen: (job: Job) => void;
}

const STATUS: Partial<Record<Job["status"], string>> = {
  queued: "排隊中",
  running: "生成中",
  failed: "失敗",
  cancelled: "已取消",
};

const tilt = tiltHandlers();

export function Gallery({ jobs, numberOf, openId, onOpen }: Props) {
  return (
    <ol className="gallery">
      {jobs.map((job) => {
        const n = numberOf(job);
        const prompt = typeof job.params.prompt === "string" ? job.params.prompt : "";
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
            <p className="tile-prompt" title={prompt}>
              {prompt}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
