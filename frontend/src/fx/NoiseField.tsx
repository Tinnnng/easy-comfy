import { useEffect, useRef } from "react";
import { createQuad, prefersReducedMotion } from "./gl";

const FRAGMENT = /* glsl */ `
uniform vec2 uRes;
uniform float uTime;
uniform float uEnergy;
uniform float uPulse;
uniform vec2 uPointer;

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  float t = uTime * (0.035 + 0.05 * uEnergy);

  // domain warping：兩層 fbm 互相扭曲，像還沒收斂的潛空間
  vec2 q = vec2(fbm(p * 1.5 + t), fbm(p * 1.5 - t + 3.1));
  vec2 r = vec2(fbm(p * 1.5 + 2.0 * q + vec2(1.7, 9.2) + t * 1.3),
                fbm(p * 1.5 + 2.0 * q + vec2(8.3, 2.8) - t));
  float f = fbm(p * 1.5 + (2.0 + uEnergy) * r);

  // 游標附近被「擾動」而變熱
  vec2 d = (uv - uPointer) * vec2(uRes.x / uRes.y, 1.0);
  f += 0.16 * exp(-dot(d, d) * 14.0);

  float heat = smoothstep(0.3, 0.95, f) * (0.58 + 0.34 * uEnergy) + uPulse * 0.22 * f * f;
  vec3 col = inferno(heat);

  // 細的掃描格線，只在能量高時浮現
  float grid = smoothstep(0.985, 1.0, max(fract(uv.x * uRes.x / 48.0), fract(uv.y * uRes.y / 48.0)));
  col += grid * 0.05 * (0.3 + uEnergy);

  col += (hash(gl_FragCoord.xy + fract(uTime)) - 0.5) * 0.04;   // 底片顆粒
  col *= 1.0 - 0.6 * length(uv - vec2(0.5, 0.42));              // 暗角
  gl_FragColor = vec4(col, 1.0);
}
`;

interface Props {
  /** 0 = 閒置，1 = 正在生成 */
  energy: number;
  /** 每次增加就觸發一次脈衝（例如取樣前進一步） */
  pulseKey: number;
}

/** 全頁背景：WebGL 的雜訊場，生成時會加速並隨步數脈動。 */
export function NoiseField({ energy, pulseKey }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const state = useRef({ energy: 0, targetEnergy: energy, pulse: 0, pointer: [0.5, 0.6], target: [0.5, 0.6] });

  useEffect(() => {
    state.current.targetEnergy = energy;
  }, [energy]);

  useEffect(() => {
    if (pulseKey) state.current.pulse = 1;
  }, [pulseKey]);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const quad = createQuad(canvas, FRAGMENT);
    if (!quad) {
      canvas.dataset.fallback = "true";
      return;
    }
    const reduced = prefersReducedMotion();
    // 背景是柔和的雜訊，用一半解析度算就夠，省下 GPU 給 ComfyUI
    const scale = 0.5;
    const resize = () => {
      canvas.width = Math.max(1, Math.floor(window.innerWidth * scale));
      canvas.height = Math.max(1, Math.floor(window.innerHeight * scale));
    };
    resize();

    const onMove = (e: PointerEvent) => {
      state.current.target = [e.clientX / window.innerWidth, 1 - e.clientY / window.innerHeight];
    };
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", onMove);

    let raf = 0;
    let last = 0;
    const start = performance.now();
    const frame = (now: number) => {
      // 背景限制在 30fps：顯示卡同時要跑 ComfyUI，能省則省
      if (now - last < 32) {
        raf = requestAnimationFrame(frame);
        return;
      }
      last = now;
      const s = state.current;
      s.energy += (s.targetEnergy - s.energy) * 0.03;
      s.pulse *= 0.94;
      s.pointer[0] += (s.target[0] - s.pointer[0]) * 0.06;
      s.pointer[1] += (s.target[1] - s.pointer[1]) * 0.06;
      const { gl, uniform } = quad;
      gl.uniform2f(uniform("uRes"), canvas.width, canvas.height);
      gl.uniform1f(uniform("uTime"), reduced ? 12 : (now - start) / 1000);
      gl.uniform1f(uniform("uEnergy"), s.energy);
      gl.uniform1f(uniform("uPulse"), s.pulse);
      gl.uniform2f(uniform("uPointer"), s.pointer[0], s.pointer[1]);
      quad.draw();
      if (!reduced && !document.hidden) raf = requestAnimationFrame(frame);
    };
    const onVisibility = () => {
      cancelAnimationFrame(raf);
      if (!document.hidden) raf = requestAnimationFrame(frame);
    };
    document.addEventListener("visibilitychange", onVisibility);
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("visibilitychange", onVisibility);
      quad.dispose();
    };
  }, []);

  return <canvas ref={canvasRef} className="noise-field" aria-hidden />;
}
