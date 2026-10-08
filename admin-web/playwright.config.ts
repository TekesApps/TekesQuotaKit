import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const port = 9470;
const db = join(mkdtempSync(join(tmpdir(), 'tq-e2e-')), 'e2e.db');

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: { baseURL: `http://127.0.0.1:${port}`, viewport: { width: 1280, height: 800 } },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: {
    // Runs from the repository root so `uv` uses the project's environment.
    command: `uv run --project .. python e2e/serve.py ${db} ${port}`,
    url: `http://127.0.0.1:${port}/user-quota/admin`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
