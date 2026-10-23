import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

interface Props {
  id: string;
  /** 已上傳到 ComfyUI 的檔名；空字串表示還沒選 */
  value: string;
  onChange: (name: string) => void;
}

const ACCEPT = "image/png,image/jpeg,image/webp";

/** 圖生圖的原圖：拖放、點擊或直接貼上，選好立刻上傳。 */
export function ImageDrop({ id, value, onChange }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(value ? `/api/inputs/${value}` : null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!ACCEPT.split(",").includes(file.type)) {
      setError("只支援 PNG、JPG 或 WebP 圖片");
      return;
    }
    setError(null);
    setBusy(true);
    const local = URL.createObjectURL(file);
    setPreview(local);
    try {
      const { name } = await api.uploadImage(file);
      onChange(name);
    } catch (e) {
      setPreview(null);
      onChange("");
      setError(e instanceof ApiError ? e.message : "上傳失敗");
    } finally {
      setBusy(false);
    }
  };

  // 在頁面任何地方 Ctrl+V 貼上圖片都會放進來
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const file = [...(e.clipboardData?.files ?? [])].find((f) => f.type.startsWith("image/"));
      if (file) {
        e.preventDefault();
        void upload(file);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  });

  useEffect(() => () => {
    if (preview?.startsWith("blob:")) URL.revokeObjectURL(preview);
  }, [preview]);

  return (
    <div className="drop-wrap">
      <button
        type="button"
        id={id}
        className={`drop${dragging ? " is-dragging" : ""}${preview ? " has-image" : ""}${busy ? " is-busy" : ""}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void upload(e.dataTransfer.files[0]);
        }}
        aria-describedby={`${id}-hint`}
      >
        {preview ? (
          <img src={preview} alt="已選的原圖" />
        ) : (
          <span className="drop-empty">
            <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden>
              <path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span>拖放圖片到這裡，或點一下選擇</span>
          </span>
        )}
        {busy && <span className="drop-status">上傳中</span>}
        <span className="drop-scan" aria-hidden />
      </button>
      <p id={`${id}-hint`} className="field-help">
        {error ? <span className="drop-error">{error}</span> : preview ? "點一下可以換一張。也可以直接 Ctrl+V 貼上。" : "也可以直接 Ctrl+V 貼上剪貼簿裡的圖片。"}
      </p>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => {
          void upload(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}
