import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Job, Template } from "./api";

interface Props {
  job: Job;
  number: number;
  template?: Template;
  onRequestClose: () => void;
  onStep: (direction: -1 | 1) => void;
  onReuse: (job: Job, keepSeed: boolean) => void;
}

/** 放大檢視：單張大圖、當時的設定、再生成。開關由 App 包在 View Transition 裡。 */
export function Loupe({ job, number, template, onRequestClose, onStep, onReuse }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [copied, setCopied] = useState(false);

  // 要在 View Transition 擷取新畫面之前就開好，所以用 layout effect
  useLayoutEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  useEffect(() => setCopied(false), [job.id]);

  const sent = typeof job.params.prompt === "string" ? job.params.prompt : "";
  const source = job.sources?.prompt;
  const prompt = source ?? sent;
  const image = job.images[0];
  const seconds =
    job.started_at && job.finished_at
      ? (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000
      : null;

  const settings = (template?.fields ?? [])
    .filter((f) => f.key !== "prompt" && job.params[f.key] !== undefined)
    .map((f) => {
      const raw = job.params[f.key];
      const option = f.options.find((o) => o.value === raw);
      return { key: f.key, label: f.label, type: f.type, value: option?.label ?? String(raw) };
    });

  const copySeed = async () => {
    try {
      await navigator.clipboard.writeText(String(job.params.seed));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <dialog
      ref={ref}
      className="loupe"
      aria-labelledby="loupe-title"
      onCancel={(e) => {
        // Esc：先攔下來，交給 App 用轉場關閉
        e.preventDefault();
        onRequestClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onRequestClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") onStep(-1);
        if (e.key === "ArrowRight") onStep(1);
      }}
    >
      <div className="loupe-inner">
        <figure className="loupe-figure">
          {image ? (
            <img
              key={image}
              src={`/outputs/${image}`}
              alt={prompt}
              style={{ viewTransitionName: `shot-${job.id}` }}
            />
          ) : (
            <div className="loupe-blank">{job.error ?? "這張沒有圖片"}</div>
          )}
        </figure>

        <div className="loupe-side">
          <div className="loupe-head">
            <h2 id="loupe-title">
              <span className="loupe-index">#{String(number).padStart(3, "0")}</span>
            </h2>
            <div className="loupe-nav">
              <button className="icon" onClick={() => onStep(-1)} aria-label="上一張（較新）">
                <Arrow dir="left" />
              </button>
              <button className="icon" onClick={() => onStep(1)} aria-label="下一張（較舊）">
                <Arrow dir="right" />
              </button>
              <button className="icon" onClick={onRequestClose} aria-label="關閉">
                <Cross />
              </button>
            </div>
          </div>

          <p className="loupe-prompt">{prompt}</p>
          {source && (
            <div className="translation">
              <span className="translation-label">自動翻譯後送給模型</span>
              <p lang="en">{sent}</p>
            </div>
          )}

          <dl className="loupe-settings">
            {settings.map((s) => (
              <div key={s.key}>
                <dt>{s.label}</dt>
                <dd>
                  {s.type === "image" ? (
                    <a href={`/api/inputs/${s.value}`} target="_blank" rel="noreferrer" className="source-thumb">
                      <img src={`/api/inputs/${s.value}`} alt="原圖" />
                    </a>
                  ) : (
                    s.value
                  )}
                  {s.key === "seed" && (
                    <button className="copy" onClick={copySeed}>
                      {copied ? "已複製" : "複製"}
                    </button>
                  )}
                </dd>
              </div>
            ))}
            {seconds !== null && job.status === "succeeded" && (
              <div>
                <dt>花費時間</dt>
                <dd>{seconds.toFixed(1)} 秒</dd>
              </div>
            )}
          </dl>

          <div className="loupe-actions">
            <button className="cta cta-block" onClick={() => onReuse(job, false)}>
              <span>換個種子再生成</span>
            </button>
            <button className="ghost" onClick={() => onReuse(job, true)}>
              把這組設定帶回編輯
            </button>
            {image && (
              <a className="ghost" href={`/outputs/${image}`} download={`easycomfy-${number}.png`}>
                下載圖片
              </a>
            )}
          </div>
        </div>
      </div>
    </dialog>
  );
}

function Arrow({ dir }: { dir: "left" | "right" }) {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden>
      <path d={dir === "left" ? "M12.5 4 6.5 10l6 6" : "M7.5 4l6 6-6 6"} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Cross() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden>
      <path d="M5 5l10 10M15 5 5 15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
