import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/camera/browser',
  timeout: 30000,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8002', viewport: { width: 390, height: 844 } },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }, { name: 'webkit', use: { browserName: 'webkit' } }],
  webServer: {
    command: '.venv/bin/uvicorn app.modules.live_camera.server:app --host 127.0.0.1 --port 8002',
    url: 'http://127.0.0.1:8002/',
    reuseExistingServer: !process.env.CI,
  },
});
