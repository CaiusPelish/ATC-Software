import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  resolve: {
    tsconfigPaths: true,
  },
  // Loopback only, so Windows Firewall never prompts (no admin on dev machines).
  server: {
    host: "127.0.0.1",
  },
});
