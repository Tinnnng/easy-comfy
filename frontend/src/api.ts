export type FieldType = "text" | "number" | "choice" | "tags" | "seed" | "image";

export interface TemplateField {
  key: string;
  label: string;
  type: FieldType;
  help: string | null;
  required: boolean;
  advanced: boolean;
  default: unknown;
  min: number | null;
  max: number | null;
  max_length: number | null;
  max_select: number | null;
  translate: boolean;
  options: { value: string; label: string }[];
}

export interface Template {
  id: string;
  name: string;
  description: string;
  heading: string;
  action: string;
  fields: TemplateField[];
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: string;
  template_id: string;
  params: Record<string, unknown>;
  status: JobStatus;
  progress: number;
  error: string | null;
  images: string[];
  /** 翻譯前的原文；params 裡是實際送給模型的英文 */
  sources: Record<string, string>;
  total_steps: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  queue_position: number | null;
}

export interface Health {
  ok: boolean;
  comfy: boolean;
  device: string | null;
  vram_total: number | null;
  vram_free: number | null;
  queue: string;
  instances: { name: string; url: string; comfy: boolean; job_id: string | null }[];
}

export class ApiError extends Error {
  constructor(message: string, public problems: string[] = []) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    if (typeof detail === "string") throw new ApiError(detail);
    if (Array.isArray(detail)) throw new ApiError("送出的資料格式不正確");
    throw new ApiError(detail?.message ?? `請求失敗（${res.status}）`, detail?.problems ?? []);
  }
  return res.json();
}

export const api = {
  health: () => request<Health>("/api/health"),
  templates: () => request<Template[]>("/api/templates"),
  jobs: () => request<Job[]>("/api/jobs"),
  createJobs: (template_id: string, params: Record<string, unknown>, count = 1) =>
    request<Job[]>("/api/jobs", { method: "POST", body: JSON.stringify({ template_id, params, count }) }),
  uploadImage: async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/uploads", { method: "POST", body });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(typeof data?.detail === "string" ? data.detail : `上傳失敗（${res.status}）`);
    return data as { name: string; url: string };
  },
  /** 把生成好的圖放進 ComfyUI 的 input 資料夾，回傳可以填進圖片欄位的檔名 */
  jobAsInput: (id: string) =>
    request<{ name: string; url: string }>(`/api/jobs/${id}/as-input`, { method: "POST" }),
  deleteJob: (id: string) => request<{ deleted: string[] }>(`/api/jobs/${id}`, { method: "DELETE" }),
  clearJobs: (statuses: JobStatus[]) =>
    request<{ deleted: string[] }>("/api/jobs/clear", { method: "POST", body: JSON.stringify({ statuses }) }),
  prioritizeJob: (id: string) => request<Job>(`/api/jobs/${id}/prioritize`, { method: "POST" }),
  cancelJob: (id: string) => request<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: "POST" }),
};

export interface JobEvents {
  onJob: (job: Job) => void;
  /** 取樣中的低解析度預覽（data URL），只有即時推播，不會存檔 */
  onPreview: (jobId: string, image: string) => void;
  /** 任務被刪除（可能是別的分頁刪的） */
  onDeleted: (jobIds: string[]) => void;
  onConnection: (up: boolean) => void;
}

/** 訂閱任務事件；斷線會自動重連。回傳取消訂閱的函式。 */
export function subscribeJobs({ onJob, onPreview, onDeleted, onConnection }: JobEvents): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry: number | undefined;

  const connect = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/ws`);
    ws.onopen = () => onConnection(true);
    ws.onmessage = (e) => {
      const event = JSON.parse(e.data);
      if (event.type === "job") onJob(event.job);
      else if (event.type === "preview") onPreview(event.job_id, event.image);
      else if (event.type === "deleted") onDeleted(event.job_ids);
    };
    ws.onclose = () => {
      onConnection(false);
      if (!closed) retry = window.setTimeout(connect, 2000);
    };
  };
  connect();

  return () => {
    closed = true;
    window.clearTimeout(retry);
    ws?.close();
  };
}
