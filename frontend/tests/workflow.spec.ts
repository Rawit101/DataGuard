import { expect, test } from '@playwright/test'

test('sample project runs through export and download', async ({page}) => {
  await page.goto('/')
  await page.getByRole('button', {name:'Load sample project'}).click()
  await expect(page.getByRole('combobox', {name:'Project'})).toContainText('Real estate demo')
  await page.getByRole('button', {name:'Pipeline Builder'}).click()
  await page.getByRole('button', {name:/Clean and summarize listings/}).click()
  await page.getByRole('button', {name:'Preview', exact:true}).click()
  await expect(page.getByText('5 total rows')).toBeVisible()
  await expect(page.getByText('Bangkok', {exact:true})).toBeVisible()
  await page.getByRole('button', {name:'Save version'}).click()
  await page.getByRole('button', {name:'Run saved version'}).click()
  await expect(page.getByText('completed', {exact:true}).first()).toBeVisible({timeout:20000})
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', {name:'Download output'}).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('property_summary.parquet')
})

test('CSV import and dataset preview work in the browser', async ({page}) => {
  await page.goto('/')
  await page.getByRole('textbox', {name:'New project name'}).fill('Browser import check')
  await page.getByRole('button', {name:'Create project'}).click()
  await page.getByRole('button', {name:'Datasets'}).click()
  await page.getByLabel('CSV or Parquet file').setInputFiles({
    name:'small.csv', mimeType:'text/csv', buffer:Buffer.from('id,province\n1,กรุงเทพมหานคร\n2,ชลบุรี\n')
  })
  await page.getByRole('button', {name:'Inspect file'}).click()
  await expect(page.getByText('2 accepted rows')).toBeVisible()
  await page.getByRole('button', {name:'Commit import'}).click()
  await expect(page.getByText('Dataset imported. The original file is preserved.')).toBeVisible()
  await expect(page.getByText('กรุงเทพมหานคร')).toBeVisible()
})
