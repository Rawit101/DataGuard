import { expect, test, type Page } from '@playwright/test'

const sections = ['Overview', 'Datasets', 'SQL Workspace', 'Data Quality', 'Pipeline Builder', 'Runs & Exports', 'Settings & Help']
const widths = [375, 768, 1024, 1440, 1920]

async function expectLayoutWithinViewport(page: Page) {
  const state = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth
    const offscreen = [...document.querySelectorAll('button, input, select, textarea, a.btn')]
      .filter(element => !element.closest('.table-wrap, .tabs, .nav, .code'))
      .filter(element => {
        const box = element.getBoundingClientRect()
        return box.width > 0 && (box.left < -1 || box.right > viewport + 1)
      })
      .map(element => element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName)
    return {documentWidth: document.documentElement.scrollWidth, viewport, offscreen}
  })
  expect(state.documentWidth).toBeLessThanOrEqual(state.viewport + 1)
  expect(state.offscreen).toEqual([])
}

test('all pages stay within five viewport widths with real sample data', async ({page, request}) => {
  test.setTimeout(90000)
  const response = await request.post('/api/demo/load')
  expect(response.ok()).toBeTruthy()
  const project = (await response.json()).project_id as string
  await page.goto('/')
  await page.getByLabel('Project', {exact: true}).selectOption(project)

  for (const width of widths) {
    await page.setViewportSize({width, height: 900})
    for (const section of sections) {
      await page.locator('nav').getByRole('button', {name: section, exact: true}).click()
      await expect(page.getByRole('heading', {name: section, level: 1})).toBeVisible()
      await expectLayoutWithinViewport(page)
    }
  }

  await page.setViewportSize({width: 1440, height: 900})
  await page.locator('nav').getByRole('button', {name: 'Overview', exact: true}).click()
  const createInput = (await page.getByRole('textbox', {name: 'New project name'}).boundingBox())!
  const createButton = (await page.getByRole('button', {name: 'Create project'}).boundingBox())!
  const renameInput = (await page.getByRole('textbox', {name: 'Project name', exact: true}).boundingBox())!
  const renameButton = (await page.getByRole('button', {name: 'Rename project'}).boundingBox())!
  expect(Math.abs(createInput.y + createInput.height - createButton.y - createButton.height)).toBeLessThan(2)
  expect(Math.abs(renameInput.y + renameInput.height - renameButton.y - renameButton.height)).toBeLessThan(2)
})

test('long Thai text, long columns, and pipeline JSON remain usable', async ({page, request}) => {
  const projectResponse = await request.post('/api/projects', {data: {name: 'Layout ภาษาไทย ' + 'โครงการ'.repeat(8)}})
  expect(projectResponse.ok()).toBeTruthy()
  const project = (await projectResponse.json()).id as string
  const column = 'long_property_description_' + 'x'.repeat(90)
  const inspection = await request.post('/api/imports/inspect', {multipart: {
    file: {name: 'very-long-filename-' + 'a'.repeat(70) + '.csv', mimeType: 'text/csv', buffer: Buffer.from(`${column},จังหวัด\n${'long text '.repeat(40)},กรุงเทพมหานคร\n`)},
    options: '{}',
  }})
  expect(inspection.ok()).toBeTruthy()
  const stage = await inspection.json()
  const committed = await request.post('/api/imports/commit', {data: {project_id: project, token: stage.token, name: 'Dataset ' + 'ชื่อยาว'.repeat(12), alias: 'long_data', type_overrides: {}}})
  expect(committed.ok()).toBeTruthy()

  await page.setViewportSize({width: 375, height: 900})
  await page.goto('/')
  await page.getByLabel('Project', {exact: true}).selectOption(project)
  await page.locator('nav').getByRole('button', {name: 'Datasets', exact: true}).click()
  await expect(page.getByText('กรุงเทพมหานคร', {exact: true}).first()).toBeVisible()
  await expectLayoutWithinViewport(page)
  const table = page.locator('.table-wrap').last()
  expect(await table.evaluate(element => element.scrollWidth > element.clientWidth)).toBeTruthy()

  await page.locator('nav').getByRole('button', {name: 'Pipeline Builder', exact: true}).click()
  await page.getByRole('button', {name: 'Add step'}).click()
  const editor = page.getByRole('textbox', {name: 'Step configuration (JSON)'})
  await expect(editor).toBeVisible()
  expect((await editor.boundingBox())!.height).toBeGreaterThanOrEqual(150)
  await editor.fill('{"type":"select","columns":["จังหวัด"],"enabled":true}')
  await expect(editor).toHaveValue(/จังหวัด/)
  await expectLayoutWithinViewport(page)

  await page.locator('nav').getByRole('button', {name: 'Overview', exact: true}).click()
  await page.evaluate(() => { document.documentElement.style.zoom = '1.25' })
  await expectLayoutWithinViewport(page)
})

test('loading and server error messages keep controls visible', async ({page}) => {
  await page.setViewportSize({width: 375, height: 900})
  await page.route('**/api/projects', async route => {
    await new Promise(resolve => setTimeout(resolve, 400))
    await route.fulfill({status: 500, contentType: 'application/json', body: '{"detail":"Temporary test error"}'})
  })
  await page.goto('/')
  await expect(page.getByText('Loading projects…')).toBeVisible()
  await expect(page.getByText(/Could not load projects/)).toBeVisible()
  await expect(page.getByRole('button', {name: 'Retry'})).toBeVisible()
  await expectLayoutWithinViewport(page)
})
