import { useEffect, useRef, useState } from "react";
import { createQuad, prefersReducedMotion } from "./gl";

const FRAGMENT = /* glsl */ `
varying vec2 vUv;
uniform sampler2D uImg;
uniform float uHasImg;
uniform float uNoise;      // 0 = 清晰的圖，1 = 純雜訊
uniform float uTime;
uniform vec2 uRes;
uniform vec2 uCover;       // object-fit: cover 的縮放
uniform float uScan;       // 定影掃描線的位置（0..1，<0 表示不顯示）

void main() {
  vec2 uv = (vUv - 0.5) * uCover + 0.5;
  uv.y = 1.0 - uv.y;

  // 雜訊越強，像素方塊越大，像還在低解析度的潛空間
  float block = mix(1.0, 26.0, uNoise * uNoise);
  vec2 cell = floor(uv * uRes / block);
  vec2 buv = (cell + 0.5) * block / uRes;

  // 雜訊越強色差越大
  float ab = 0.014 * uNoise;
  vec3 img = vec3(texture2D(uImg, buv + vec2(ab, 0.0)).r,
                  texture2D(uImg, buv).g,
                  texture2D(uImg, buv - vec2(ab, 0.0)).b) * uHasImg;

  // 每個方塊有固定的溶解門檻，所以畫面是一塊一塊「收斂」出來，不會整片淡入
  float threshold = hash(cell * 0.73 + 0.17);
  float live = hash(cell + floor(uTime * 18.0));
  vec3 static_ = inferno(0.15 + live * 0.75 * (0.4 + 0.6 * fbm(uv * 3.0 + uTime * 0.2)));
  // 乘上 1.1 讓 uNoise=0 時完全乾淨、uNoise=1 時完全是雜訊
  float dissolve = smoothstep(threshold, threshold + 0.1, uNoise * 1.1);
  vec3 col = mix(img, static_, dissolve);

  // 掃描線：亮的一條加上後面的餘暉
  if (uScan >= 0.0) {
    float d = uv.y - uScan;
    col += vec3(1.0, 0.85, 0.55) * (smoothstep(0.012, 0.0, abs(d)) * 0.9 + smoothstep(-0.18, 0.0, d) * step(d, 0.0) * 0.12);
  }

  gl_FragColor = vec4(col, 1.0);
}
`;

interface Props {
  /** 要顯示的圖（data URL 或網址）；null 表示只有雜訊 */
  src: string | null;
  /** 目標雜訊量，畫面會平滑地趨近 */
  noise: number;
  /** 改變時播放一次定影掃描 */
  revealKey?: string;
  alt: string;
  className?: string;
}

/** 用 WebGL 把圖片和熱圖雜訊混合，呈現「去雜訊」的過程。不支援 WebGL 時退回一般的 img。 */
export function DenoiseCanvas({ src, noise, revealKey, alt, className }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [fallback, setFallback] = useState(false);
  const api = useRef<{ setImage: (src: string | null) => void; reveal: () => void } | null>(null);
  const target = useRef(noise);
  target.current = noise;

  useEffect(() => {
    const canvas = canvasRef.current!;
    const quad = createQuad(canvas, FRAGMENT);
    if (!quad) {
      setFallback(true);
      return;
    }
    const { gl, uniform } = quad;
    const reduced = prefersReducedMotion();

    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 255]));

    let imgAspect = 1;
    let hasImg = 0;
    let current = target.current;
    let scan = -1;
    let latest = 0;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    api.current = {
      setImage: (next) => {
        const ticket = ++latest;
        if (!next) {
          hasImg = 0;
          return;
        }
        const image = new Image();
        image.onload = () => {
          if (ticket !== latest) return; // 已經有更新的圖
          gl.bindTexture(gl.TEXTURE_2D, texture);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
          imgAspect = image.naturalWidth / image.naturalHeight;
          hasImg = 1;
        };
        image.src = next;
      },
      reveal: () => {
        if (!reduced) scan = 0;
      },
    };

    let raf = 0;
    const start = performance.now();
    const frame = (now: number) => {
      current = reduced ? target.current : current + (target.current - current) * 0.06;
      if (Math.abs(target.current - current) < 0.002) current = target.current;
      if (scan >= 0) scan = scan > 1.2 ? -1 : scan + 0.018;

      const canvasAspect = canvas.width / canvas.height;
      const cover = canvasAspect > imgAspect ? [1, imgAspect / canvasAspect] : [canvasAspect / imgAspect, 1];
      gl.uniform1i(uniform("uImg"), 0);
      gl.uniform1f(uniform("uHasImg"), hasImg);
      gl.uniform1f(uniform("uNoise"), current);
      gl.uniform1f(uniform("uTime"), reduced ? 0 : (now - start) / 1000);
      gl.uniform2f(uniform("uRes"), canvas.width, canvas.height);
      gl.uniform2f(uniform("uCover"), cover[0], cover[1]);
      gl.uniform1f(uniform("uScan"), scan);
      quad.draw();
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      gl.deleteTexture(texture);
      quad.dispose();
      api.current = null;
    };
  }, []);

  useEffect(() => {
    api.current?.setImage(src);
  }, [src, fallback]);

  useEffect(() => {
    if (revealKey) api.current?.reveal();
  }, [revealKey]);

  if (fallback) {
    return src ? <img className={className} src={src} alt={alt} /> : <div className={className} />;
  }
  return <canvas ref={canvasRef} className={className} role="img" aria-label={alt} />;
}
