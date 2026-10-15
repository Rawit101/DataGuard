import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'

let runtimeErrors: string[] = []
test.beforeEach(({page}) => {
  runtimeErrors = []
  page.on('pageerror', error => runtimeErrors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) runtimeErrors.push(message.text())
  })
})
test.afterEach(() => { expect(runtimeErrors).toEqual([]) })

async function project(request: APIRequestContext, name = 'Audit actual data') {
  const response = await request.post('/api/projects', {data: {name}})
  expect(response.ok()).toBeTruthy()
  return (await response.json()).id as string
}

async function upload(request: APIRequestContext, id: string, alias: string, contents: string) {
  const response = await request.post('/api/imports/inspect', {multipart: {
    file: {name: `${alias}.csv`, mimeType: 'text/csv', buffer: Buffer.from(contents)}, options: '{}',
  }})
  expect(response.ok()).toBeTruthy()
  const stage = await response.json()
  const committed = await request.post('/api/imports/commit', {data: {
    project_id: id, token: stage.token, name: alias, alias, type_overrides: {},
  }})
  expect(committed.ok()).toBeTruthy()
  return (await committed.json()).id as string
}

async function open(page: Page, id: string, section: string) {
  await page.goto('/')
  await page.getByLabel('Project', {exact: true}).selectOption(id)
  await page.locator('nav').getByRole('button', {name: section, exact: true}).click()
}

test('fresh CSV quality execution, downloaded failures, and historical snapshot persist', async ({page, request}) => {
  const id = await project(request, 'Audit uploaded Thai CSV')
  await open(page, id, 'Datasets')
  await page.getByLabel('CSV or Parquet file').setInputFiles({name: 'new-customers.csv', mimeType: 'text/csv',
    buffer: Buffer.from('id,customer,amount\nA,ลูกค้า,10\nA,,bad\nB,เชียงใหม่,20\nbroken,too,many,fields\n')})
  await page.getByRole('button', {name: 'Inspect file'}).click()
  await expect(page.getByText(/3 accepted rows · 1 structural issues/)).toBeVisible()
  await page.getByLabel('Type for amount').selectOption('DOUBLE')
  await page.getByRole('button', {name: 'Commit import'}).click()
  await expect(page.getByText('Dataset imported. The original file is preserved.')).toBeVisible()
  await expect(page.getByText('เชียงใหม่', {exact: true})).toBeVisible()
  const ds = (await (await request.get(`/api/projects/${id}/datasets`)).json())[0]
  expect(ds.row_count).toBe(3)
  expect(ds.reject_count).toBe(2)
  await page.reload()
  await page.locator('nav').getByRole('button', {name: 'Data Quality', exact: true}).click()
  await page.getByLabel('Rule name').fill('Customer is required')
  await page.getByLabel('Column', {exact: true}).selectOption('customer')
  await page.getByRole('button', {name: 'Create rule'}).click()
  await page.getByRole('button', {name: 'Run check'}).first().click()
  await expect(page.getByRole('heading', {name: 'Latest result: Customer is required'})).toBeVisible()
  await expect(page.getByText('33.3%', {exact: true})).toBeVisible()
  const downloaded = page.waitForEvent('download')
  await page.getByRole('link', {name: 'Download all failures'}).click()
  const download = await downloaded
  const contents = await readFile((await download.path())!, 'utf8')
  expect(contents).toContain('A,,bad')
  expect(contents).not.toContain('เชียงใหม่')

  await page.getByLabel('Rule name').fill('Edited customer check')
  await page.getByRole('button', {name: 'Save changes'}).click()
  await expect(page.getByText('Quality rule saved', {exact: true})).toBeVisible()
  await page.reload()
  await page.locator('nav').getByRole('button', {name: 'Data Quality', exact: true}).click()
  await page.getByRole('button', {name: 'Edited customer check', exact: true}).click()
  await expect(page.getByRole('heading', {name: 'Stored result: Customer is required'})).toBeVisible()
  await expect(page.getByText('33.3%', {exact: true})).toBeVisible()
  await expect(page.getByRole('link', {name: 'Download all failures'})).toBeVisible()
})

test('a delayed profile cannot overwrite a newly selected dataset', async ({page, request}) => {
  const id = await project(request)
  const first = await upload(request, id, 'first_profile', 'amount\n1\n')
  await upload(request, id, 'second_profile', 'amount\n99\n')
  let release!: () => void
  let arrived!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const started = new Promise<void>(resolve => { arrived = resolve })
  await page.route(`**/api/datasets/${first}/profile`, async route => {
    const response = await route.fetch(); arrived(); await gate; await route.fulfill({response})
  })
  await open(page, id, 'Datasets')
  await page.getByRole('button', {name: 'first_profile · 1 rows', exact: true}).click()
  await page.getByRole('button', {name: 'Profile', exact: true}).click()
  await started
  await page.getByRole('button', {name: 'second_profile · 1 rows', exact: true}).click()
  await expect(page.locator('tbody').last()).toContainText('99')
  const finished = page.waitForResponse(response => response.url().endsWith(`/datasets/${first}/profile`))
  release(); await finished; await page.waitForTimeout(100)
  await expect(page.locator('tbody').last()).toContainText('99')
})

test('quality history ignores a late response for the previous rule', async ({page, request}) => {
  const id = await project(request)
  const dataset = await upload(request, id, 'checks', 'id\nA\nA\nB\n')
  const rules: {id: string; name: string}[] = []
  for (const [name, kind, config] of [['Old unique', 'unique', {columns: ['id']}], ['New required', 'required', {column: 'id'}]] as const) {
    const response = await request.post('/api/quality-rules', {data: {project_id: id, dataset_id: dataset, name, kind, config, severity: 'error'}})
    const rule = await response.json(); rules.push({id: rule.id, name})
    expect((await request.post(`/api/quality-rules/${rule.id}/run`)).ok()).toBeTruthy()
  }
  let release!: () => void
  let arrived!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const started = new Promise<void>(resolve => { arrived = resolve })
  await page.route(`**/api/quality-rules/${rules[0].id}/runs`, async route => {
    const response = await route.fetch(); arrived(); await gate; await route.fulfill({response})
  })
  await open(page, id, 'Data Quality')
  await page.getByRole('button', {name: rules[0].name, exact: true}).click(); await started
  await page.getByRole('button', {name: rules[1].name, exact: true}).click()
  await expect(page.getByRole('heading', {name: 'Stored result: New required'})).toBeVisible()
  const finished = page.waitForResponse(response => response.url().endsWith(`/quality-rules/${rules[0].id}/runs`))
  release(); await finished; await page.waitForTimeout(100)
  await expect(page.getByRole('heading', {name: 'Stored result: New required'})).toBeVisible()
  await expect(page.getByRole('heading', {name: 'Stored result: Old unique'})).toHaveCount(0)
})

test('row-count failures are shown as dataset checks instead of a false row percentage', async ({page, request}) => {
  const id = await project(request)
  const dataset = await upload(request, id, 'empty', 'id\n')
  const rule = await (await request.post('/api/quality-rules', {data: {project_id: id, dataset_id: dataset,
    name: 'At least one row', kind: 'row_count', config: {min: 1}, severity: 'error'}})).json()
  await open(page, id, 'Data Quality')
  await page.getByRole('button', {name: 'Run check'}).first().click()
  await expect(page.getByText('Failed checks', {exact: true})).toBeVisible()
  await expect(page.getByText('0%', {exact: true})).toHaveCount(0)
  const actual = (await (await request.get(`/api/quality-rules/${rule.id}/runs`)).json())[0]
  expect(actual.status).toBe('failed'); expect(actual.evaluated_rows).toBe(0); expect(actual.failed_rows).toBe(1)
})

test('pipeline loading locks editing and run selection ignores late details', async ({page, request}) => {
  const id = await project(request)
  const dataset = await upload(request, id, 'run_source', 'id\n1\n2\n')
  const jobs: {pipeline: string; run: string; name: string}[] = []
  for (const name of ['Older pipeline', 'Newer pipeline']) {
    const saved = await request.post('/api/pipelines', {data: {project_id: id, name, definition: {
      source_dataset_id: dataset, steps: [], pre_rule_ids: [], post_rule_ids: [], strict_quality: false,
      output: {format: 'csv', filename: `${name}.csv`},
    }}})
    expect(saved.ok()).toBeTruthy()
    const pipeline = (await saved.json()).id as string
    const queued = await request.post(`/api/pipelines/${pipeline}/run`, {data: {}})
    const run = (await queued.json()).id as string
    await expect.poll(async () => (await (await request.get(`/api/runs/${run}`)).json()).status).toBe('completed')
    jobs.push({pipeline, run, name})
  }
  let release!: () => void
  let arrived!: () => void
  let gate = new Promise<void>(resolve => { release = resolve })
  let started = new Promise<void>(resolve => { arrived = resolve })
  await page.route(`**/api/pipelines/${jobs[0].pipeline}`, async route => {
    const response = await route.fetch(); arrived(); await gate; await route.fulfill({response})
  })
  await open(page, id, 'Pipeline Builder')
  await page.getByRole('button', {name: /Older pipeline v1/}).click(); await started
  await expect(page.getByLabel('Name', {exact: true})).toBeDisabled()
  await expect(page.getByRole('button', {name: 'New pipeline', exact: true})).toBeDisabled()
  release()
  await expect(page.getByLabel('Name', {exact: true})).toHaveValue('Older pipeline')
  await expect(page.getByLabel('Name', {exact: true})).toBeEnabled()

  gate = new Promise<void>(resolve => { release = resolve })
  started = new Promise<void>(resolve => { arrived = resolve })
  await page.route(`**/api/runs/${jobs[0].run}`, async route => {
    const response = await route.fetch(); arrived(); await gate; await route.fulfill({response})
  })
  await page.locator('nav').getByRole('button', {name: 'Runs & Exports', exact: true}).click()
  const newestHeading = page.getByRole('heading', {name: `Run ${jobs[1].run.slice(0, 12)}`, exact: true})
  await expect(newestHeading).toBeVisible()
  await page.getByRole('button', {name: jobs[0].name, exact: true}).click(); await started
  await expect(newestHeading).toHaveCount(0)
  await page.getByRole('button', {name: jobs[1].name, exact: true}).click()
  await expect(newestHeading).toBeVisible()
  const finished = page.waitForResponse(response => response.url().endsWith(`/runs/${jobs[0].run}`))
  release(); await finished; await page.waitForTimeout(100)
  await expect(newestHeading).toBeVisible()
  const actual = await (await request.get(`/api/runs/${jobs[1].run}`)).json()
  await expect(page.getByRole('link', {name: 'Download output', exact: true})).toHaveAttribute('href', `/api/artifacts/${actual.output_artifact_id}/download`)
})

test('backend errors remain visible and settings retry reads actual configuration', async ({page, request}) => {
  const id = await project(request)
  const endpoints = [`**/api/projects/${id}/overview`, `**/api/projects/${id}/datasets`, `**/api/projects/${id}/runs`, '**/api/settings']
  for (const endpoint of endpoints) await page.route(endpoint, route => route.fulfill({status: 500, contentType: 'text/plain', body: 'Audit backend unavailable'}))
  await page.goto('/')
  await page.getByLabel('Project', {exact: true}).selectOption(id)
  for (const [section, marker] of [['Overview', 'Retry overview'], ['Datasets', 'Audit backend unavailable'], ['SQL Workspace', 'Retry SQL datasets'], ['Data Quality', 'Retry quality data'], ['Pipeline Builder', 'Retry pipeline data'], ['Runs & Exports', 'Retry runs and exports'], ['Settings & Help', 'Retry settings']]) {
    await page.locator('nav').getByRole('button', {name: section, exact: true}).click()
    await expect(page.locator('main').getByText(marker, {exact: false}).last()).toBeVisible()
  }
  await page.unroute('**/api/settings')
  await page.getByRole('button', {name: 'Retry settings'}).click()
  const actual = await (await request.get('/api/settings')).json()
  await expect(page.getByText(`Version ${actual.schema_version}`, {exact: true})).toBeVisible()
})

test('empty projects use true empty states and disable unsupported actions', async ({page, request}) => {
  const id = await project(request, 'Audit empty workspace')
  await open(page, id, 'Datasets')
  await expect(page.getByText('No datasets yet', {exact: true})).toBeVisible()
  await page.locator('nav').getByRole('button', {name: 'SQL Workspace', exact: true}).click()
  await expect(page.getByText('Import a dataset first.', {exact: true})).toBeVisible()
  await expect(page.getByRole('button', {name: 'Run query'})).toBeDisabled()
  await page.locator('nav').getByRole('button', {name: 'Pipeline Builder', exact: true}).click()
  await expect(page.getByRole('button', {name: 'Save pipeline'})).toBeDisabled()
  await page.locator('nav').getByRole('button', {name: 'Runs & Exports', exact: true}).click()
  await expect(page.getByText('No runs yet', {exact: true})).toBeVisible()
  await expect(page.getByText('No artifacts yet', {exact: true})).toBeVisible()
})
