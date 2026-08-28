import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

// DEMO=1 npm run dev  →  展示端口 4174，文件监听关闭，HMR 关闭
// npm run dev         →  编写端口 5174，HMR 开启
const isDemo = process.env.DEMO === "1";

const PORT = isDemo ? 4174 : 5174;

const PROXY = {
  "/api/v2": {
    target: "http://127.0.0.1:5001",
    changeOrigin: true,
    timeout: 120000,
  },
  "/api": {
    target: "http://127.0.0.1:5000",
    changeOrigin: true,
    timeout: 60000,
  },
};

export default defineConfig({
  plugins: [tailwindcss(), reactRouter(), tsconfigPaths()],
  server: {
    host: "0.0.0.0",
    port: PORT,
    strictPort: true,
    allowedHosts: ["dissyllabic-disgustedly-setsuko.ngrok-free.dev"],
    // 展示模式：关闭 HMR 和文件监听，启动后内容冻结
    hmr: isDemo ? false : { protocol: "ws", timeout: 30000, overlay: false },
    watch: isDemo ? null : { usePolling: false, ignored: ["**/node_modules/**", "**/.git/**", "**/uploads/**"] },
    proxy: PROXY,
  },
  optimizeDeps: {
    include: ["@matejmazur/react-katex", "katex", "vis-network"],
  },
});
