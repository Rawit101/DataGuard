import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

export default defineConfig({
  testDir: 'tests',
  timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:8000', headless: true },
  webServer: { command: '.venv/bin/python scripts/start.py', cwd: '..',
               env: {DATAGUARD_WORKSPACE: process.env.DATAGUARD_WORKSPACE || resolve('..', 'work', 'playwright-workspace')},
               url: 'http://127.0.0.1:8000/api/health', timeout: 30000,
               reuseExistingServer: false },
  reporter: 'list'
})
