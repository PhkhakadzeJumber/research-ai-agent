import type { NextConfig } from "next";

// Where the Eureka API runs. The browser only ever talks to this site: /api/* is forwarded to the backend, so the
// login cookie stays first-party (a cookie set by another domain would be blocked by browsers). Step 10a.
const backend = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  // a self-contained server in .next/standalone: what the Docker image runs (frontend/Dockerfile)
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
