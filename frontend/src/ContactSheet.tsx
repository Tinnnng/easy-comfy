import type { Job } from "./api";

interface Props {
  jobs: Job[];
  numberOf: (job: Job) => number;
  onOpen: (job: Job) => void;
}

const NOTE: Partial<Record<Job["status"], string>> = {
  queued: "排隊中",
  running: "顯影中",
  failed: "失敗",
  cancelled: "取消",
};

/** 生成紀錄排成底片印樣：每格是一張圖，下面用蠟筆寫著當時的描述。 */
export function ContactSheet({ jobs, numberOf, onOpen }: Props) {
  return (
    <ol className="sheet">
      {jobs.map((job) => {
        const n = numberOf(job);
        const prompt = typeof job.params.prompt === "string" ? job.params.prompt : "";
        const image = job.images[0];
        return (
          <li key={job.id} className={`sheet-item state-${job.status}`}>
            <button className="sheet-frame" onClick={() => onOpen(job)} aria-label={`第 ${n} 張：${prompt}`}>
              <span className="film">
                <span className="edge" aria-hidden>
                  <span>{n}</span>
                  <span>{n}A</span>
                </span>
                <span className="frame">
                  {image ? (
                    <img src={`/outputs/${image}`} alt="" loading="lazy" decoding="async" />
                  ) : (
                    <span className="frame-blank">{NOTE[job.status]}</span>
                  )}
                </span>
                <span className="edge" aria-hidden>
                  <span />
                  <span />
                </span>
              </span>
              <GreaseMark />
            </button>
            <p className="sheet-note" title={prompt}>
              {prompt}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

/** 滑過或聚焦時，用紅色蠟筆圈起來，就像在印樣上挑片。 */
export function GreaseMark() {
  return (
    <svg className="grease" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
      <path
        d="M 10 46 C 8 17, 38 4, 55 5 C 82 7, 98 25, 95 53 C 92 82, 64 97, 44 95 C 17 92, 3 72, 7 45 C 9 33, 16 24, 27 18"
      />
    </svg>
  );
}
