import type { Config } from "@react-router/dev/config";

export default {
  // Config options...
  // Server-side render by default, to enable SPA mode set this to `false`
  ssr: process.env.DESKTOP_SPA === "1" ? false : true,
} satisfies Config;
