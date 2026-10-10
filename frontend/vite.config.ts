import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const { VITE_BACKEND_URL } = loadEnv(mode, process.cwd(), "VITE_");

  return {
    plugins: [tailwindcss(), reactRouter()],
    resolve: {
      tsconfigPaths: true,
    },
    // Loopback only, so Windows Firewall never prompts (no admin on dev machines).
    server: {
      host: "127.0.0.1",
      // Keep browser API requests same-origin; Vite forwards them to FastAPI.
      proxy: {
        "/api": VITE_BACKEND_URL || "http://127.0.0.1:8000",
      },
    },
  };
});
