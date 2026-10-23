import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { prefersReducedMotion } from "./gl";

const GLYPHS = "░▒▓█<>/\\=+*01潛噪影像步採樣解碼";

/** 文字從亂碼逐字解碼成正確內容。只在掛載時播放一次。 */
export function Scramble({ text, duration = 1100, className }: { text: string; duration?: number; className?: string }) {
  const [shown, setShown] = useState(() => (prefersReducedMotion() ? text : ""));

  useEffect(() => {
    if (prefersReducedMotion()) {
      setShown(text);
      return;
    }
    const chars = [...text];
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const settled = Math.floor(t * chars.length);
      setShown(
        chars
          .map((c, i) => (i < settled ? c : i < settled + 4 ? GLYPHS[Math.floor(Math.random() * GLYPHS.length)] : " "))
          .join(""),
      );
      if (t < 1) raf = requestAnimationFrame(tick);
      else setShown(text);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [text, duration]);

  return (
    <span className={className}>
      <span className="visually-hidden">{text}</span>
      <span aria-hidden>{shown}</span>
    </span>
  );
}

/** 數字平滑地滾到目標值（遙測數字用）。resetKey 改變時直接跳到新值，不從舊數字滾過去。 */
export function useTween(value: number, resetKey?: string, speed = 0.15) {
  const [shown, setShown] = useState(value);
  const current = useRef(value);
  const lastKey = useRef(resetKey);
  useEffect(() => {
    if (prefersReducedMotion() || lastKey.current !== resetKey) {
      lastKey.current = resetKey;
      current.current = value;
      setShown(value);
      return;
    }
    let raf = 0;
    const tick = () => {
      current.current += (value - current.current) * speed;
      if (Math.abs(value - current.current) < 0.001) current.current = value;
      setShown(current.current);
      if (current.current !== value) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, resetKey, speed]);
  return shown;
}

/** 卡片跟著游標 3D 傾斜，並移動反光的位置。把回傳的事件處理器掛到元素上即可。 */
export function tiltHandlers(max = 9) {
  const set = (el: HTMLElement, x: number, y: number, active: boolean) => {
    el.style.setProperty("--rx", `${(-y * max).toFixed(2)}deg`);
    el.style.setProperty("--ry", `${(x * max).toFixed(2)}deg`);
    el.style.setProperty("--gx", `${((x + 0.5) * 100).toFixed(1)}%`);
    el.style.setProperty("--gy", `${((y + 0.5) * 100).toFixed(1)}%`);
    el.dataset.tilt = active ? "on" : "off";
  };
  return {
    onPointerMove: (e: ReactPointerEvent<HTMLElement>) => {
      if (e.pointerType !== "mouse" || prefersReducedMotion()) return;
      const r = e.currentTarget.getBoundingClientRect();
      set(e.currentTarget, (e.clientX - r.left) / r.width - 0.5, (e.clientY - r.top) / r.height - 0.5, true);
    },
    onPointerLeave: (e: ReactPointerEvent<HTMLElement>) => set(e.currentTarget, 0, 0, false),
  };
}

/** 按鈕被游標輕輕吸過去。 */
export function magneticHandlers(strength = 0.25) {
  return {
    onPointerMove: (e: ReactPointerEvent<HTMLElement>) => {
      if (e.pointerType !== "mouse" || prefersReducedMotion()) return;
      const r = e.currentTarget.getBoundingClientRect();
      const x = (e.clientX - r.left - r.width / 2) * strength;
      const y = (e.clientY - r.top - r.height / 2) * strength;
      e.currentTarget.style.translate = `${x.toFixed(1)}px ${y.toFixed(1)}px`;
    },
    onPointerLeave: (e: ReactPointerEvent<HTMLElement>) => {
      e.currentTarget.style.translate = "";
    },
  };
}
