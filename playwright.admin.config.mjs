import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/admin',
  timeout: 30000,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8006', viewport: { width: 1280, height: 900 } },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
  ],
  webServer: {
    command: `${process.env.PYTHON || (process.platform === 'win32' ? 'python' : '.venv/bin/python')} -m http.server 8006 --bind 127.0.0.1 --directory app/modules/admin/static`,
    url: 'http://127.0.0.1:8006/',
    reuseExistingServer: false,
  },
});
