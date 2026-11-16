/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Self-contained server bundle for the production image (frontend/Dockerfile "prod" target).
  output: "standalone",
};

module.exports = nextConfig;
