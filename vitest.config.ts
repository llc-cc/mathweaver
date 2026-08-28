import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

// The app Vite config starts React Router's development integration, which
// keeps `vitest run` alive after the assertions finish.  Unit tests only need
// the TypeScript/JSX transform, so keep their runner deliberately minimal.
export default defineConfig({
  plugins: [tsconfigPaths()],
  test: {
    environment: "node",
    pool: "forks",
    fileParallelism: false,
  },
});
