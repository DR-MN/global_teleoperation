/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // No "output: standalone": Render runs `next start`, which serves the normal
  // .next build. Standalone is only for copying server.js into a minimal Docker
  // image and triggers a warning under `next start`.
};
module.exports = nextConfig;
