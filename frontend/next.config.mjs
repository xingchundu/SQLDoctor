/** @type {import('next').NextConfig} */
const backendPort = process.env.API_PORT || "8010";
const nextConfig = {
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
