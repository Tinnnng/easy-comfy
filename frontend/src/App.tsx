import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { api, ApiError, subscribeJobs, type Health, type Job, type Template } from "./api";
import { Composer } from "./Composer";
import { DevelopPanel } from "./DevelopPanel";
import { NoiseField } from "./fx/NoiseField";
import { prefersReducedMotion } from "./fx/gl";
import { Gallery } from "./Gallery";
import { Loupe } from "./Loupe";

interface FormState {
  templateId: string;
  values?: Record<string, unknown>;
  version: number; // 變動時重新建立表單，套用新的初始值
}

const STAGE: Record<Job["status"], number> = { queued: 0, running: 1, succeeded: 2, failed: 2, cancelled: 2 };

/**
 * 送單的 HTTP 回應可能比 WebSocket 的「開始執行」事件還晚到，
 * 這時回應裡的 queued 是舊資料，不能蓋掉已經收到的新狀態。
 */
function isStale(known: Job | undefined, incoming: Job) {
  if (!known) return false;
  const a = STAGE[known.status], b = STAGE[incoming.status];
  return b < a || (a === 1 && b === 1 && incoming.progress < known.progress);
}

function optionLabel(template: Template | undefined, key: string, value: unknown) {
  return template?.fields.find((f) => f.key === key)?.options.find((o) => o.value === value)?.label ?? "";
}

/** 用 View Transition 包住狀態更新；不支援或使用者偏好減少動態時直接更新。 */
function withTransition(update: () => void): Promise<void> {
  const doc = document as Document & {
    startViewTransition?: (cb: () => void) => { updateCallbackDone: Promise<void> };
  };
  if (!doc.startViewTransition || prefersReducedMotion()) {
    update();
    return Promise.resolve();
  }
  return doc.startViewTransition(() => flushSync(update)).updateCallbackDone;
}

export function App() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [jobs, setJobs] = useState<Map<string, Job>>(new Map());
  const [previews, setPreviews] = useState<Map<string, string>>(new Map());
  const [health, setHealth] = useState<Health | null>(null);
  const [live, setLive] = useState(true);
  const [form, setForm] = useState<FormState | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [loupeId, setLoupeId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const developRef = useRef<HTMLDivElement>(null);

  const upsertJob = useCallback((job: Job) => {
    setJobs((prev) => (isStale(prev.get(job.id), job) ? prev : new Map(prev).set(job.id, job)));
  }, []);

  useEffect(() => {
    api
      .templates()
      .then((ts) => {
        setTemplates(ts);
        if (ts.length) setForm((f) => f ?? { templateId: ts[0].id, version: 0 });
      })
      .catch((e) => setError(e));
    api.jobs().then((js) => setJobs(new Map(js.map((j) => [j.id, j])))).catch(() => {});
    return subscribeJobs({
      onJob: upsertJob,
      onPreview: (jobId, image) => setPreviews((prev) => new Map(prev).set(jobId, image)),
      onConnection: (up) => {
        setLive(up);
        // 重新連上時補抓一次，避免斷線期間漏掉的更新
        if (up) api.jobs().then((js) => js.forEach(upsertJob)).catch(() => {});
      },
    });
  }, [upsertJob]);

  useEffect(() => {
    const check = () => api.health().then(setHealth).catch(() => setHealth(null));
    check();
    const timer = window.setInterval(check, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const newestFirst = useMemo(
    () => [...jobs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [jobs],
  );
  const numbers = useMemo(
    () => new Map(newestFirst.map((j, i) => [j.id, newestFirst.length - i])),
    [newestFirst],
  );
  const numberOf = useCallback((job: Job) => numbers.get(job.id) ?? 0, [numbers]);
  const templateById = useMemo(() => new Map(templates.map((t) => [t.id, t])), [templates]);
  const current = form ? templateById.get(form.templateId) : undefined;

  // 顯影台：自己剛送出的那張優先，其次是正在跑的任務，都沒有就擺最近完成的作品
  const focusJob =
    (focusId ? jobs.get(focusId) : undefined) ??
    newestFirst.find((j) => j.status === "running") ??
    newestFirst.find((j) => j.status === "succeeded") ??
    null;
  const focusTemplate = focusJob ? templateById.get(focusJob.template_id) : current;
  const dims = optionLabel(focusTemplate, "size", focusJob?.params.size ?? "square").match(/(\d+)\s*[×x]\s*(\d+)/);
  const steps = optionLabel(focusTemplate, "quality", focusJob?.params.quality ?? "normal").match(/(\d+)\s*步/);
  const busy = newestFirst.some((j) => j.status === "running");
  const runningStep = Math.round((newestFirst.find((j) => j.status === "running")?.progress ?? 0) * 100);

  const loupeIndex = loupeId ? newestFirst.findIndex((j) => j.id === loupeId) : -1;
  const loupeJob = loupeIndex >= 0 ? newestFirst[loupeIndex] : null;

  async function create(templateId: string, values: Record<string, unknown>) {
    setError(null);
    try {
      const job = await api.createJob(templateId, values);
      upsertJob(job);
      setFocusId(job.id);
      // 窄螢幕上顯影台在表單下面，送出後捲過去讓人看到進度
      if (window.matchMedia("(max-width: 960px)").matches) {
        developRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(String(e)));
      return false;
    }
  }

  async function submit(values: Record<string, unknown>) {
    if (!current) return;
    setSubmitting(true);
    await create(current.id, values);
    setSubmitting(false);
  }

  function reuse(job: Job, keepSeed: boolean) {
    withTransition(() => setLoupeId(null));
    if (!keepSeed) {
      void create(job.template_id, { ...job.params, seed: -1 });
    } else {
      setForm((f) => ({ templateId: job.template_id, values: { ...job.params }, version: (f?.version ?? 0) + 1 }));
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  async function cancel(job: Job) {
    try {
      await api.cancelJob(job.id);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(String(e)));
    }
  }

  return (
    <div className="app">
      <NoiseField energy={busy ? 1 : 0} pulseKey={runningStep} />

      <header className="masthead">
        <span className="wordmark">
          Easy<span>Comfy</span>
        </span>
        <GpuStatus health={health} live={live} busy={busy} />
      </header>

      <main>
        <div className="bench">
          <section className="compose" aria-label="建立新圖片">
            {templates.length > 1 && (
              <div className="tabs" role="tablist" aria-label="模板">
                {templates.map((t) => (
                  <button
                    key={t.id}
                    role="tab"
                    aria-selected={t.id === form?.templateId}
                    className="tab"
                    onClick={() => setForm((f) => ({ templateId: t.id, version: (f?.version ?? 0) + 1 }))}
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            )}

            {current ? (
              <Composer
                key={`${current.id}-${form?.version}`}
                template={current}
                initialValues={form?.values}
                submitting={submitting}
                onSubmit={submit}
              />
            ) : (
              <p className="muted">{error ? "" : "正在載入模板"}</p>
            )}

            {health && !health.comfy && (
              <p className="callout">
                還連不上 ComfyUI。請在 StabilityMatrix 的 Packages 頁面按下 ComfyUI 的 Launch，看到網址出現後再生成。
              </p>
            )}
            {error && (
              <div className="callout callout-error" role="alert">
                <p>{error.message}</p>
                {error.problems.length > 0 && (
                  <ul>
                    {error.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>

          <div ref={developRef}>
            <DevelopPanel
              job={focusJob}
              aspect={dims ? [Number(dims[1]), Number(dims[2])] : [1, 1]}
              preview={focusJob ? previews.get(focusJob.id) : undefined}
              totalSteps={steps ? Number(steps[1]) : 4}
              recent={!!focusJob && focusJob.id !== focusId}
              onCancel={cancel}
              onOpen={(j) => withTransition(() => setLoupeId(j.id))}
            />
          </div>
        </div>

        <section className="archive" aria-labelledby="archive-title">
          <div className="archive-head">
            <h2 id="archive-title">生成紀錄</h2>
            {newestFirst.length > 0 && <span className="count">{newestFirst.length} 張</span>}
          </div>
          {newestFirst.length === 0 ? (
            <p className="muted">生成的每一張圖都會留在這裡，可以隨時放大、下載，或用同一組設定再生成。</p>
          ) : (
            <Gallery
              jobs={newestFirst}
              numberOf={numberOf}
              openId={loupeId}
              onOpen={(j) => withTransition(() => setLoupeId(j.id))}
            />
          )}
        </section>
      </main>

      {loupeJob && (
        <Loupe
          job={loupeJob}
          number={numberOf(loupeJob)}
          template={templateById.get(loupeJob.template_id)}
          onRequestClose={() => {
            const lastId = loupeJob.id;
            // 關閉後把焦點還給最後看的那張卡片，鍵盤使用者才不會迷路
            void withTransition(() => setLoupeId(null)).then(() =>
              document.querySelector<HTMLElement>(`.tile-card[data-job="${lastId}"]`)?.focus({ preventScroll: true }),
            );
          }}
          onStep={(d) => {
            const next = newestFirst[loupeIndex + d];
            if (next) setLoupeId(next.id);
          }}
          onReuse={reuse}
        />
      )}
    </div>
  );
}

/** 頁首右側：ComfyUI 連線、顯示卡名稱與即時 VRAM 用量。 */
function GpuStatus({ health, live, busy }: { health: Health | null; live: boolean; busy: boolean }) {
  if (!health?.comfy) {
    return (
      <div className="gpu is-offline" role="status">
        <span className="gpu-dot" aria-hidden />
        ComfyUI 未連線
      </div>
    );
  }
  const total = health.vram_total ?? 0;
  const used = total && health.vram_free !== null ? total - health.vram_free : 0;
  const ratio = total ? used / total : 0;
  const gb = (n: number) => (n / 1024 ** 3).toFixed(1);
  return (
    <div className={busy ? "gpu is-busy" : "gpu"} role="status">
      <span className="gpu-dot" aria-hidden />
      <span className="gpu-name">{shortDevice(health.device ?? "GPU")}</span>
      {total > 0 && (
        <span className="gpu-vram" title="顯示卡記憶體用量">
          <span className="gpu-meter" aria-hidden>
            <span style={{ transform: `scaleX(${ratio})` }} />
          </span>
          <span className="gpu-num">
            {gb(used)}/{gb(total)} GB
          </span>
        </span>
      )}
      {!live && <span className="gpu-warn">即時更新重新連線中</span>}
    </div>
  );
}

function shortDevice(name: string) {
  // ComfyUI 回傳像 "cuda:0 NVIDIA GeForce RTX 4060 Ti : cudaMallocAsync"
  return name.replace(/^cuda:\d+\s*/, "").replace(/\s*:.*$/, "").replace("NVIDIA GeForce ", "");
}
