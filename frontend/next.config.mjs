/** @type {import('next').NextConfig} */
const backendPort = String(process.env.API_PORT || "8010").trim();
const defaultDevOrigin = `http://127.0.0.1:${backendPort}`;
/** 开发环境默认直连 FastAPI，避免 rewrite 失败时整页 HTML 500 被当成错误文案 */
const publicBackend = (
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  (process.env.NODE_ENV !== "production" ? defaultDevOrigin : "")
).trim();

const nextConfig = {
  env: {
    NEXT_PUBLIC_BACKEND_URL: publicBackend,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `http://127.0.0.1:${backendPort}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
