import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'

async function setup(request: APIRequestContext, name: string) {
  const response = await request.post('/api/projects', {data: {name}})
  expect(response.ok()).toBeTruthy()
  return (await response.json()).id as string
}

async function upload(request: APIRequestContext, project: string, name: string, contents: string) {
  const inspection = await request.post('/api/imports/inspect', {multipart: {
    file: {name: `${name}.csv`, mimeType: 'text/csv', buffer: Buffer.from(contents)}, options: '{}'
  }})
  expect(inspection.ok()).toBeTruthy()
  const stage = await inspection.json()
  const committed = await request.post('/api/imports/commit', {data: {
    project_id: project, token: stage.token, name, alias: name, type_overrides: {}
  }})
  expect(committed.ok()).toBeTruthy()
  return (await committed.json()).id as string
}

async function open(page: Page, project: string, section: string) {
  await page.goto('/')
  await expect(page.getByLabel('Project', {exact:true})).toContainText('Audit')
  await page.getByLabel('Project', {exact:true}).selectOption(project)
  await page.getByRole('button', {name: section, exact: true}).click()
}

test('dataset switching resets incompatible filters and never shows old rows', async ({page, request}) => {
  const project = await setup(request, 'Audit dataset switch')
  await upload(request, project, 'customers', 'customer\nAlice\nBob\n')
  await upload(request, project, 'cities', 'city\nเชียงใหม่\nขอนแก่น\n')
  await open(page, project, 'Datasets')
  await page.getByRole('button', {name: 'customers · 2 rows', exact: true}).click()
  await page.getByLabel('Filter column').selectOption('customer')
  await page.getByLabel('Contains', {exact:true}).fill('Alice')
  await expect(page.locator('tbody').last()).toContainText('Alice')
  await page.getByRole('button', {name: 'cities · 2 rows', exact: true}).click()
  await expect(page.getByLabel('Filter column')).toHaveValue('')
  await expect(page.getByLabel('Contains', {exact:true})).toHaveValue('')
  await expect(page.locator('tbody').last()).toContainText('เชียงใหม่')
  await expect(page.locator('tbody').last()).not.toContainText('Alice')
})

test('running a rule from the list binds the editor to that exact rule', async ({page, request}) => {
  const project = await setup(request, 'Audit rule editor')
  const dataset = await upload(request, project, 'checks', 'id,amount\nA,10\nB,bad\n')
  const rules = [
    {name: 'Identity is present', kind: 'required', config: {column: 'id', blank_is_missing: true}},
    {name: 'Amount is numeric', kind: 'parse_valid', config: {column: 'amount', type: 'DOUBLE'}}
  ]
  const ids: string[] = []
  for (const rule of rules) {
    const response = await request.post('/api/quality-rules', {data: {
      project_id: project, dataset_id: dataset, severity: 'warning', ...rule
    }})
    expect(response.ok()).toBeTruthy()
    ids.push((await response.json()).id)
  }
  await open(page, project, 'Data Quality')
  await page.getByRole('button', {name: rules[0].name, exact:true}).click()
  const card = page.locator('.step').filter({has:page.getByRole('button', {name:rules[1].name, exact:true})})
  await card.getByRole('button', {name:'Run check'}).click()
  await expect(page.getByRole('heading', {name:`Latest result: ${rules[1].name}`})).toBeVisible()
  await expect(page.getByLabel('Rule name')).toHaveValue(rules[1].name)
  await expect(page.getByLabel('Check', {exact:true})).toHaveValue('parse_valid')
  await expect(page.getByLabel('Column', {exact:true})).toHaveValue('amount')
  await page.getByRole('button', {name:'Save changes'}).click()
  await expect(page.getByRole('status')).toContainText('Quality rule saved')
  const saved = await (await request.get(`/api/projects/${project}/quality-rules`)).json()
  expect(saved.find((rule:{id:string}) => rule.id === ids[1])).toMatchObject(rules[1])
})

test('all quality configuration forms execute real checks and download failures', async ({page, request}) => {
  const project = await setup(request, 'Audit quality forms')
  const dataset = await upload(request, project, 'form_checks', 'id,amount,zone\nA,10,N\nA,bad,X\n,30,S\n')
  const reference = await upload(request, project, 'zones', 'zone\nN\nS\n')
  await open(page, project, 'Data Quality')
  await page.getByLabel('Dataset', {exact:true}).selectOption(dataset)
  const cases = [
    {kind:'required', column:'id', failed:1},
    {kind:'unique', failed:2},
    {kind:'parse_valid', column:'amount', failed:1},
    {kind:'range', column:'amount', failed:2},
    {kind:'accepted', column:'zone', failed:1},
    {kind:'regex', column:'id', failed:0},
    {kind:'foreign_key', column:'zone', failed:1},
    {kind:'row_count', failed:1}
  ]
  for (const item of cases) {
    await page.getByRole('button', {name:'New', exact:true}).click()
    await page.getByLabel('Rule name').fill(`Audit ${item.kind}`)
    await page.getByLabel('Check', {exact:true}).selectOption(item.kind)
    if (item.column) await page.getByLabel('Column', {exact:true}).selectOption(item.column)
    if (item.kind === 'unique') await page.getByLabel('Key columns, comma separated').fill('id')
    if (item.kind === 'range') {
      await page.getByLabel('Minimum', {exact:true}).fill('0')
      await page.getByLabel('Maximum', {exact:true}).fill('20')
    }
    if (item.kind === 'accepted') await page.getByLabel('Accepted values, comma separated').fill('N,S')
    if (item.kind === 'regex') await page.getByLabel('Full-match pattern').fill('[A-Z]')
    if (item.kind === 'foreign_key') {
      await page.getByLabel('Reference dataset', {exact:true}).selectOption(reference)
      await page.getByLabel('Reference column', {exact:true}).selectOption('zone')
    }
    if (item.kind === 'row_count') await page.getByLabel('Minimum rows').fill('4')
    const savedResponse = page.waitForResponse(r => r.url().endsWith('/api/quality-rules') && r.request().method() === 'POST')
    await page.getByRole('button', {name:'Create rule'}).click()
    expect((await savedResponse).ok()).toBeTruthy()
    const runResponse = page.waitForResponse(r => /\/quality-rules\/[^/]+\/run$/.test(r.url()))
    await page.getByRole('button', {name:'Run check', exact:true}).last().click()
    const result = await (await runResponse).json()
    expect(result.failed_rows).toBe(item.failed)
    await expect(page.getByRole('heading', {name:`Latest result: Audit ${item.kind}`})).toBeVisible()
    if (item.kind === 'parse_valid') {
      const pending = page.waitForEvent('download')
      await page.getByRole('link', {name:'Download all failures'}).click()
      const download = await pending
      const contents = await readFile((await download.path())!, 'utf8')
      expect(contents).toContain('bad')
      expect(contents).toContain('Audit parse_valid')
    }
  }
})

test('browser SQL script download actually completes with executable SQL', async ({page, request}) => {
  const response = await request.post('/api/demo/load')
  expect(response.ok()).toBeTruthy()
  const project = (await response.json()).project_id
  await page.goto('/')
  await page.getByLabel('Project', {exact:true}).selectOption(project)
  await page.getByRole('button', {name:'Pipeline Builder', exact:true}).click()
  await page.getByRole('button', {name:/Clean and summarize listings/}).click()
  await page.getByRole('button', {name:'Preview', exact:true}).click()
  await expect(page.getByText('5 total rows')).toBeVisible()
  const pending = page.waitForEvent('download')
  await page.getByRole('button', {name:'Download runnable SQL'}).click()
  const download = await pending
  expect(download.suggestedFilename()).toBe('pipeline.sql')
  const sql = await readFile((await download.path())!, 'utf8')
  expect(sql).toContain('CREATE OR REPLACE VIEW')
  expect(sql).toContain('row_number() OVER')
  expect(sql).toContain('read_parquet')
})

test('SQL save and delete plus full CSV BOM export through the browser', async ({page, request}) => {
  const project = await setup(request, 'Audit full CSV download')
  const csv = 'id,label\n' + Array.from({length:120}, (_,i)=>`${i+1},ทดสอบ${i+1}`).join('\n') + '\n'
  await upload(request, project, 'many_rows', csv)
  await open(page, project, 'SQL Workspace')
  await page.getByRole('textbox', {name:'Query',exact:true}).fill('SELECT id,label FROM many_rows ORDER BY CAST(id AS BIGINT)')
  await page.getByRole('button', {name:'Run query'}).click()
  await expect(page.getByText(/120 total rows/)).toBeVisible()
  await page.getByLabel('Save as').fill('Audit query')
  await page.getByRole('button', {name:'Save query', exact:true}).click()
  await expect(page.getByLabel('Saved query')).not.toHaveValue('')
  await page.getByLabel('Export format').selectOption('csv')
  await page.getByLabel('UTF-8 BOM for spreadsheet apps').check()
  const pending = page.waitForEvent('download')
  await page.getByRole('button', {name:'Export full result'}).click()
  const download = await pending
  const bytes = await readFile((await download.path())!)
  expect([...bytes.subarray(0,3)]).toEqual([239,187,191])
  expect(bytes.toString('utf8').trim().split('\n')).toHaveLength(121)
  expect(bytes.toString('utf8')).toContain('120,ทดสอบ120')
  await page.getByRole('button', {name:'Delete', exact:true}).click()
  await expect(page.getByLabel('Saved query')).toHaveValue('')
  expect(await (await request.get(`/api/projects/${project}/queries`)).json()).toEqual([])
})

test('discard import preview removes it without creating a dataset', async ({page, request}) => {
  const project = await setup(request, 'Audit discard')
  await open(page, project, 'Datasets')
  await page.getByLabel('CSV or Parquet file').setInputFiles({
    name:'discard.csv', mimeType:'text/csv', buffer:Buffer.from('id\n1\n')
  })
  await page.getByRole('button', {name:'Inspect file'}).click()
  await expect(page.getByRole('heading', {name:/Review import/})).toBeVisible()
  await page.getByRole('button', {name:'Discard preview'}).click()
  await expect(page.getByRole('heading', {name:/Review import/})).toHaveCount(0)
  expect(await (await request.get(`/api/projects/${project}/datasets`)).json()).toEqual([])
})

test('a late SQL response cannot display or export results for an edited query', async ({page, request}) => {
  const project = await setup(request, 'Audit SQL response ordering')
  await upload(request, project, 'items', 'id\nA\nB\n')
  await open(page, project, 'SQL Workspace')
  await expect(page.getByRole('button', {name:'Run query'})).toBeEnabled()
  let release!: () => void
  const held = new Promise<void>(resolve=>{release=resolve})
  await page.route('**/api/queries/run', async route=>{
    const response = await route.fetch()
    await held
    await route.fulfill({response})
  })
  await page.getByRole('textbox', {name:'Query',exact:true}).fill("SELECT id FROM items WHERE id='A'")
  const pending = page.waitForResponse(r=>r.url().endsWith('/api/queries/run'))
  await page.getByRole('button', {name:'Run query'}).click()
  await page.getByRole('textbox', {name:'Query',exact:true}).fill("SELECT id FROM items WHERE id='B'")
  release()
  await pending
  await expect(page.getByRole('button', {name:'Run query'})).toBeEnabled()
  await expect(page.getByRole('heading', {name:'Results',exact:true})).toHaveCount(0)
  await page.unroute('**/api/queries/run')
  await page.getByRole('button', {name:'Run query'}).click()
  await expect(page.locator('tbody')).toContainText('B')
  await expect(page.locator('tbody')).not.toContainText('A')
})

test('fresh Thai data cleans, joins, aggregates and exports through the pipeline UI', async ({page, request}) => {
  const project = await setup(request, 'Audit independent pipeline')
  const source = await upload(request, project, 'sales', 'id,amount,zone\nA,100,N\nB,250,N\nB,250,N\nC,150,S\nD,bad,S\nE,400,X\n')
  const reference = await upload(request, project, 'regions', 'zone,name\nN,เหนือ\nS,ใต้\n')
  await open(page, project, 'Pipeline Builder')
  await page.getByLabel('Source dataset').selectOption(source)
  await page.getByLabel('Name', {exact:true}).fill('Audit clean join sum')
  const steps = [
    {type:'cast', column:'amount', target_type:'DOUBLE', failure_policy:'drop'},
    {type:'deduplicate', keys:['id'], keep:'first'},
    {type:'join', reference_dataset_id:reference, keys:[{left:'zone',right:'zone'}], prefix:'ref_', join_type:'left'},
    {type:'fill', values:{ref_name:'ไม่ทราบ'}},
    {type:'group', group_by:['ref_name'], aggregations:[{function:'sum',column:'amount',as:'total'}]},
    {type:'sort', by:[{column:'ref_name',direction:'asc'}]}
  ]
  for (const step of steps) {
    await page.getByLabel('Operation', {exact:true}).selectOption(step.type)
    await page.getByRole('button', {name:'Add step'}).click()
    await page.getByRole('textbox', {name:'Step configuration (JSON)',exact:true}).last().fill(JSON.stringify(step))
  }
  await page.getByRole('button', {name:'Preview', exact:true}).click()
  await expect(page.getByText('3 total rows')).toBeVisible()
  const expected = [{ref_name:'เหนือ',total:350,__source_count:2}, {ref_name:'ใต้',total:150,__source_count:1}, {ref_name:'ไม่ทราบ',total:400,__source_count:1}]
  const saveResponse = page.waitForResponse(r=>r.url().endsWith('/api/pipelines') && r.request().method()==='POST')
  await page.getByRole('button', {name:'Save pipeline'}).click()
  const pipeline = (await (await saveResponse).json()).id
  const runResponse = page.waitForResponse(r=>/\/pipelines\/[^/]+\/run$/.test(new URL(r.url()).pathname))
  await page.getByRole('button', {name:'Run saved version'}).click()
  const run = (await (await runResponse).json()).id
  await expect.poll(async()=> (await (await request.get(`/api/runs/${run}`)).json()).status).toBe('completed')
  await expect(page.getByRole('link', {name:'Download output'})).toBeVisible()
  const pending = page.waitForEvent('download')
  await page.getByRole('link', {name:'Download output'}).click()
  const downloaded = await pending
  expect(await downloaded.failure()).toBeNull()
  const artifact = (await (await request.get(`/api/runs/${run}`)).json()).output_artifact_id
  expect((await request.get(`/api/artifacts/${artifact}/download`)).ok()).toBeTruthy()
  await page.getByRole('button', {name:'Pipeline Builder', exact:true}).click()
  await page.getByRole('button', {name:/Audit clean join sum/}).click()
  await page.getByLabel('Output format').selectOption('csv')
  await page.getByText('UTF-8 BOM', {exact:true}).click()
  await expect(page.getByRole('checkbox', {name:'UTF-8 BOM',exact:true})).toBeChecked()
  await page.getByRole('button', {name:'Save version'}).click()
  await expect(page.getByRole('button', {name:'Run saved version'})).toBeEnabled()
  const secondResponse = page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(`/api/pipelines/${pipeline}/run`))
  await page.getByRole('button', {name:'Run saved version'}).click()
  const second = (await (await secondResponse).json()).id
  await expect.poll(async()=> (await (await request.get(`/api/runs/${second}`)).json()).status).toBe('completed')
  const csvArtifact = (await (await request.get(`/api/runs/${second}`)).json()).output_artifact_id
  const bytes = await (await request.get(`/api/artifacts/${csvArtifact}/download`)).body()
  expect([...bytes.subarray(0,3)]).toEqual([239,187,191])
  const actual = bytes.toString('utf8').replace(/^\uFEFF/,'').trim().split('\n').slice(1).map(line=>{
    const [ref_name,total,count] = line.split(',')
    return {ref_name,total:Number(total),__source_count:Number(count)}
  })
  expect(actual.sort((a,b)=>a.total-b.total)).toEqual(expected.sort((a,b)=>a.total-b.total))
})

test('actual SQL export and pipeline jobs cancel from browser controls', async ({page, request}) => {
  test.setTimeout(60000)
  const project = await setup(request, 'Audit browser cancellation')
  const contents = 'n,join_key\n' + Array.from({length:10000},(_,i)=>`${i},1`).join('\n') + '\n'
  const left = await upload(request, project, 'big_left', contents)
  const right = await upload(request, project, 'big_right', contents)
  await open(page, project, 'SQL Workspace')
  await expect(page.getByRole('button', {name:'Run query'})).toBeEnabled()
  await page.getByRole('textbox', {name:'Query',exact:true}).fill('SELECT a.n AS left_n,b.n AS right_n FROM big_left a CROSS JOIN big_right b')
  await page.getByRole('button', {name:'Run query'}).click()
  await expect(page.getByText(/100,000,000 total rows/)).toBeVisible()
  await page.getByLabel('Export format').selectOption('csv')
  const exportResponse = page.waitForResponse(r=>r.url().endsWith('/api/queries/export'))
  await page.getByRole('button', {name:'Export full result'}).click()
  const job = (await (await exportResponse).json()).id
  await expect.poll(async()=> (await (await request.get(`/api/export-jobs/${job}`)).json()).status).toBe('running')
  await page.getByRole('button', {name:'Cancel export', exact:true}).click()
  await expect.poll(async()=> (await (await request.get(`/api/export-jobs/${job}`)).json()).status).toBe('cancelled')
  const pipelineResponse = await request.post('/api/pipelines', {data:{
    project_id:project,name:'Audit long pipeline',definition:{
      source_dataset_id:left,steps:[{type:'join',reference_dataset_id:right,join_type:'inner',prefix:'ref_',keys:[{left:'join_key',right:'join_key'}],cardinality:'many_to_many'}],
      pre_rule_ids:[],post_rule_ids:[],strict_quality:false,output:{format:'csv',filename:'cancelled.csv'}
    }
  }})
  expect(pipelineResponse.ok()).toBeTruthy()
  const pipeline = (await pipelineResponse.json()).id
  await page.getByRole('button', {name:'Pipeline Builder', exact:true}).click()
  await page.getByRole('button', {name:/Audit long pipeline/}).click()
  const runResponse = page.waitForResponse(r=>new URL(r.url()).pathname.endsWith(`/api/pipelines/${pipeline}/run`))
  await page.getByRole('button', {name:'Run saved version'}).click()
  const run = (await (await runResponse).json()).id
  await expect.poll(async()=> (await (await request.get(`/api/runs/${run}`)).json()).status).toBe('running')
  await page.getByRole('button', {name:'Cancel', exact:true}).click()
  await expect.poll(async()=> (await (await request.get(`/api/runs/${run}`)).json()).status).toBe('cancelled')
  const detail = await (await request.get(`/api/runs/${run}`)).json()
  expect(detail.output_artifact_id).toBeNull()
  expect(await (await request.get(`/api/projects/${project}/artifacts`)).json()).toEqual([])
})
