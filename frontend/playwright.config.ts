import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'tests',
  timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:8000', headless: true },
  webServer: { command: '.venv/bin/python scripts/start.py', cwd: '..',
               url: 'http://127.0.0.1:8000/api/health', timeout: 30000,
               reuseExistingServer: false },
  reporter: 'list'
})
