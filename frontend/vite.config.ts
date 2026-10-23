import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 開發時把 API、WebSocket、圖片請求轉給後端，前端就不用處理 CORS
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", ws: true },
      "/outputs": "http://127.0.0.1:8000",
    },
  },
});
