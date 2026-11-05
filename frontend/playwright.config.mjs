import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

const root = resolve(import.meta.dirname, '..');
const python = process.env.JX3_PYTHON || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
export default defineConfig({
  testDir: './tests',
  timeout: 90000,
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8765', browserName: 'chromium', channel: process.env.JX3_BROWSER_CHANNEL || 'msedge', trace: 'retain-on-failure' },
  webServer: {
    command: `"${python}" -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --workers 1`,
    cwd: root,
    url: 'http://127.0.0.1:8765/api/health',
    reuseExistingServer: false,
    env: { JX3_DATA_DIR: resolve(root, '.tools', `browser-test-${randomUUID()}`) },
  },
});
