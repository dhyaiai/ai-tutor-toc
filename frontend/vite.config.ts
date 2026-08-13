import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Vite 6 默认拒绝非 localhost 域名访问;cloudflared 隧道使用 *.trycloudflare.com 域名,需放行
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("error", (err) => {
            console.error("[vite proxy error]", err.message);
          });
        },
      },
    },
  },
});
