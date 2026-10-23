import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, subscribeJobs, type Health, type Job, type Template } from "./api";
import { Composer } from "./Composer";
import { ContactSheet } from "./ContactSheet";
import { DevelopFrame } from "./DevelopFrame";
import { Loupe } from "./Loupe";

interface FormState {
  templateId: string;
  values?: Record<string, unknown>;
  version: number; // 變動時重新建立表單，套用新的初始值
}

/** 從選項文字找出數字，例如「方形 1024×1024」或「標準（4 步）」 */
function optionLabel(template: Template | undefined, key: string, value: unknown) {
  return template?.fields.find((f) => f.key === key)?.options.find((o) => o.value === value)?.label ?? "";
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

  const upsertJob = useCallback((job: Job) => {
    setJobs((prev) => new Map(prev).set(job.id, job));
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
    const timer = window.setInterval(check, 10000);
    return () => window.clearInterval(timer);
  }, []);

  const newestFirst = useMemo(
    () => [...jobs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [jobs],
  );
  // 底片編號依生成順序，最舊的是第 1 張
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
  const dims = optionLabel(focusTemplate, "size", focusJob?.params.size ?? form?.values?.size ?? "square")
    .match(/(\d+)\s*[×x]\s*(\d+)/);
  const steps = optionLabel(focusTemplate, "quality", focusJob?.params.quality).match(/(\d+)\s*步/);

  const loupeIndex = loupeId ? newestFirst.findIndex((j) => j.id === loupeId) : -1;
  const loupeJob = loupeIndex >= 0 ? newestFirst[loupeIndex] : null;

  async function create(templateId: string, values: Record<string, unknown>) {
    setError(null);
    try {
      const job = await api.createJob(templateId, values);
      upsertJob(job);
      setFocusId(job.id);
      // 窄螢幕上顯影台在表單下面，送出後捲過去讓人看到進度
      if (window.matchMedia("(max-width: 900px)").matches) {
        document.querySelector(".develop")?.scrollIntoView({ behavior: "smooth", block: "center" });
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
    setLoupeId(null);
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

  const comfyLabel = health?.comfy
    ? `ComfyUI 已連線${health.device ? `（${shortDevice(health.device)}）` : ""}`
    : "ComfyUI 未連線";

  return (
    <div className="app">
      <header className="masthead">
        <span className="wordmark">EasyComfy</span>
        <div className="lamps">
          <span className={health?.comfy ? "lamp on" : "lamp"}>{comfyLabel}</span>
          {!live && <span className="lamp">即時更新中斷，正在重新連線</span>}
        </div>
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
              <p className="quiet-text">{error ? "" : "正在載入模板"}</p>
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

          <DevelopFrame
            job={focusJob}
            frameNumber={focusJob ? numberOf(focusJob) : null}
            aspect={dims ? `${dims[1]} / ${dims[2]}` : "1 / 1"}
            preview={focusJob ? previews.get(focusJob.id) : undefined}
            totalSteps={steps ? Number(steps[1]) : undefined}
            recent={!!focusJob && focusJob.id !== focusId}
            onCancel={cancel}
            onOpen={(j) => setLoupeId(j.id)}
          />
        </div>

        <section className="archive" aria-labelledby="archive-title">
          <div className="archive-head">
            <h2 id="archive-title">生成紀錄</h2>
            {newestFirst.length > 0 && <span className="count">{newestFirst.length} 張</span>}
          </div>
          {newestFirst.length === 0 ? (
            <p className="quiet-text">生成的每一張圖都會留在這裡，可以隨時放大、下載，或用同一組設定再生成。</p>
          ) : (
            <ContactSheet jobs={newestFirst} numberOf={numberOf} onOpen={(j) => setLoupeId(j.id)} />
          )}
        </section>
      </main>

      {loupeJob && (
        <Loupe
          job={loupeJob}
          number={numberOf(loupeJob)}
          template={templateById.get(loupeJob.template_id)}
          onClose={() => setLoupeId(null)}
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

function shortDevice(name: string) {
  // ComfyUI 回傳像 "cuda:0 NVIDIA GeForce RTX 4060 Ti : cudaMallocAsync"
  return name.replace(/^cuda:\d+\s*/, "").replace(/\s*:.*$/, "").replace("NVIDIA GeForce ", "");
}
