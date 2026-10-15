import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

const evidence = process.env.DATAGUARD_EVIDENCE_DIR
async function capture(page: Page, name: string) {
  if (evidence) { await mkdir(evidence, {recursive: true}); await page.setViewportSize({width:1440,height:900}); await page.evaluate(()=>window.scrollTo(0,0)); await page.screenshot({path: join(evidence, name), fullPage: true}) }
}
async function upload(request: APIRequestContext, project: string, alias: string, buffer: Buffer, extension = 'csv') {
  const stage = await request.post('/api/imports/inspect', {multipart: {
    file: {name: `${alias}.${extension}`, mimeType: 'application/octet-stream', buffer}, options: '{}',
  }})
  expect(stage.ok()).toBeTruthy()
  const result = await request.post('/api/imports/commit', {data: {project_id: project,
    token: (await stage.json()).token, name: alias, alias, type_overrides: {}}})
  expect(result.ok()).toBeTruthy()
  return (await result.json()).id as string
}
async function open(page: Page, project: string, section: string) {
  await page.goto('/')
  await page.getByLabel('Project', {exact: true}).selectOption(project)
  await page.locator('nav').getByRole('button', {name: section, exact: true}).click()
}
async function selectPipeline(page: Page, name: string) {
  await page.getByRole('region', {name: 'Saved pipelines', exact: true}).getByRole('button', {name: new RegExp(`^${name} v`)}).click()
  await expect(page.getByLabel('Name', {exact: true})).toHaveValue(name)
}
async function execute(page: Page, request: APIRequestContext, id: string, version: number, extra: number) {
  const pending = page.waitForResponse(r => r.url().endsWith(`/api/pipelines/${id}/run?version=${version}`))
  await page.getByRole('button', {name: 'Run saved version', exact: true}).click()
  const response = await pending
  expect(response.ok()).toBeTruthy()
  const run = (await response.json()).id
  await expect.poll(async () => (await (await request.get(`/api/runs/${run}`)).json()).status).toBe('completed')
  const result = await (await request.get(`/api/runs/${run}`)).json()
  expect(result.version).toBe(version)
  await expect(page.getByRole('heading', {name: `Run ${run.slice(0,12)}`, exact: true})).toBeVisible()
  await expect(page.getByRole('link', {name: 'Download output', exact: true})).toBeVisible({timeout: 20000})
  const download = await request.get(`/api/artifacts/${result.output_artifact_id}/download`)
  expect(download.ok()).toBeTruthy()
  const lines = (await download.text()).trim().split(/\r?\n/)
  const column = lines[0].split(',').indexOf('computed')
  expect(lines.slice(1).map(line => Number(line.split(',')[column])).sort((a,b) => a-b)).toEqual([10+extra,20+extra])
  return {id: run, version, status: result.status}
}

test('five independent pipelines preserve versions and execute saved configurations', async ({page, request}) => {
  test.setTimeout(120000)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if(message.type()==='error')errors.push(message.text()) })
  const project = (await (await request.post('/api/projects', {data: {name: 'Pipeline persistence regression'}})).json()).id
  const source = await upload(request, project, 'source', Buffer.from('id,amount\n1,10\n2,20\n'))
  const sentinel = (await (await request.post('/api/pipelines', {data: {project_id: project, name: 'Existing isolated pipeline',
    definition: {source_dataset_id: source, steps: [], pre_rule_ids: [], post_rule_ids: [], strict_quality: true,
      output: {format: 'csv', filename: 'existing.csv'}}}})).json()).id
  const sentinelBefore = await (await request.get(`/api/pipelines/${sentinel}`)).json()
  await open(page, project, 'Pipeline Builder')
  const ids: string[] = []
  for (let index=1;index<=5;index++) {
    await page.getByRole('button', {name: 'New pipeline', exact: true}).click()
    const draft = (await page.getByText(/^Draft ID:/).innerText()).match(/[0-9a-f]{32}/)![0]
    const name = `pipeline_test_${String(index).padStart(2,'0')}`
    await page.getByLabel('Name', {exact: true}).fill(name)
    await page.getByLabel('Output format').selectOption('csv')
    await page.getByLabel('Output filename').fill(`${name}.csv`)
    for (const step of [
      {type: 'cast', column: 'amount', target_type: 'DOUBLE', failure_policy: 'error'},
      {type: 'calculate', as: 'computed', expression: {op: 'add', left: {column: 'amount'}, right: {value: index}}},
    ]) {
      await page.getByLabel('Operation', {exact: true}).selectOption(step.type)
      await page.getByRole('button', {name: 'Add step', exact: true}).click()
      await page.getByRole('textbox', {name: 'Step configuration (JSON)', exact: true}).last().fill(JSON.stringify(step))
    }
    const pending = page.waitForResponse(r => r.url().endsWith('/api/pipelines') && r.request().method()==='POST')
    await page.getByRole('button', {name: 'Save pipeline', exact: true}).click()
    const response = await pending
    expect(response.ok()).toBeTruthy()
    const saved = await response.json()
    expect(saved.id).toBe(draft); expect(saved.version).toBe(1); ids.push(saved.id)
    await expect(page.getByRole('button', {name: 'Save version', exact: true})).toBeEnabled()
  }
  expect(new Set(ids).size).toBe(5)
  const before = await Promise.all(ids.map(async id => (await request.get(`/api/pipelines/${id}`)).json()))
  await selectPipeline(page, 'pipeline_test_01')
  await page.getByRole('textbox', {name: 'Step configuration (JSON)', exact: true}).last().fill(JSON.stringify({
    type: 'calculate', as: 'computed', expression: {op: 'add', left: {column: 'amount'}, right: {value: 100}},
  }))
  const versionResponse = page.waitForResponse(r => r.url().endsWith(`/api/pipelines/${ids[0]}`) && r.request().method()==='PUT')
  await page.getByRole('button', {name: 'Save version', exact: true}).click()
  expect((await (await versionResponse).json()).version).toBe(2)
  await expect(page.getByLabel('Saved version')).toHaveValue('2')
  for (let index=1;index<5;index++) expect(await (await request.get(`/api/pipelines/${ids[index]}`)).json()).toEqual(before[index])
  expect(await (await request.get(`/api/pipelines/${sentinel}`)).json()).toEqual(sentinelBefore)
  await capture(page, 'five-pipelines-v2.png')
  await page.getByLabel('Saved version').selectOption('1')
  await expect(page.getByLabel('Saved version')).toHaveValue('1')
  expect(JSON.parse(await page.getByRole('textbox', {name: 'Step configuration (JSON)', exact: true}).last().inputValue()).expression.right.value).toBe(1)
  await execute(page, request, ids[0], 1, 1)
  await page.locator('nav').getByRole('button', {name: 'Pipeline Builder', exact: true}).click()
  await selectPipeline(page, 'pipeline_test_01')
  await page.getByLabel('Saved version').selectOption('1')
  await page.getByRole('button', {name: 'Restore as new version', exact: true}).click()
  await expect(page.getByLabel('Saved version')).toHaveValue('3')
  expect((await (await request.get(`/api/pipelines/${ids[0]}`)).json()).versions.map((v: {version:number}) => v.version)).toEqual([3,2,1])
  await page.getByLabel('Saved version').selectOption('1')
  const duplicated = page.waitForResponse(r => r.url().endsWith(`/api/pipelines/${ids[0]}/duplicate?version=1`))
  await page.getByRole('button', {name: 'Duplicate pipeline', exact: true}).click()
  const copy = (await (await duplicated).json()).id
  expect(ids).not.toContain(copy)
  expect((await (await request.get(`/api/pipelines/${copy}`)).json()).definition).toEqual(before[0].definition)
  await page.reload()
  await page.locator('nav').getByRole('button', {name: 'Pipeline Builder', exact: true}).click()
  for (let index=1;index<=5;index++) await expect(page.getByRole('region', {name: 'Saved pipelines'}).getByRole('button', {name: new RegExp(`^pipeline_test_0${index} v`)})).toBeVisible()
  const executions = []
  for (let index=1;index<=5;index++) {
    await selectPipeline(page, `pipeline_test_0${index}`)
    executions.push(await execute(page, request, ids[index-1], index===1?3:1, index))
    await page.locator('nav').getByRole('button', {name: 'Pipeline Builder', exact: true}).click()
  }
  expect(await (await request.get(`/api/pipelines/${sentinel}`)).json()).toEqual(sentinelBefore)
  for (let index=1;index<5;index++) expect(await (await request.get(`/api/pipelines/${ids[index]}`)).json()).toEqual(before[index])
  if (evidence) await writeFile(join(evidence, 'five-pipelines.json'), JSON.stringify({project,ids,sentinel,copy,executions},null,2))
  expect(errors).toEqual([])
})

test('severity and passed failed running error statuses are distinct with real backend calculations', async ({page, request}) => {
  test.setTimeout(60000)
  const errors:string[]=[]
  page.on('pageerror',error=>errors.push(error.message))
  page.on('console',message=>{if(message.type()==='error'&&!message.text().includes('Failed to load resource'))errors.push(message.text())})
  const project = (await (await request.post('/api/projects', {data: {name: 'Quality labeling regression'}})).json()).id
  const source = await upload(request, project, 'checks', Buffer.from('id,amount\n1,10\n1,20\n2,30\n'))
  const create = async (name:string,kind:string,severity:string,config:Record<string,unknown>,dataset=source) => {
    const response = await request.post('/api/quality-rules', {data: {project_id: project, dataset_id: dataset, name, kind, severity, config}})
    expect(response.ok()).toBeTruthy(); return (await response.json()).id as string
  }
  const passing = await create('Error severity, passing rows','required','error',{column:'id'})
  await create('Warning severity, duplicate rows','unique','warning',{columns:['id']})
  const job = await (await request.post('/api/queries/export', {data:{project_id:project,sql:'SELECT CAST(id AS BIGINT) AS id FROM checks',format:'parquet',name:'typed.parquet'}})).json()
  await expect.poll(async()=> (await (await request.get(`/api/export-jobs/${job.id}`)).json()).status).toBe('completed')
  const artifact = (await (await request.get(`/api/export-jobs/${job.id}`)).json()).artifact_id
  const typed = await upload(request,project,'typed_keys',await (await request.get(`/api/artifacts/${artifact}/download`)).body(),'parquet')
  const reference = await upload(request,project,'bad_reference',Buffer.from('id\nbad\n'))
  const erroneous = await create('Actual execution conversion error','foreign_key','error',{column:'id',reference_dataset_id:reference,reference_column:'id'},typed)
  await open(page,project,'Data Quality')
  const card = (name:string)=> page.locator('.step').filter({has:page.getByRole('button',{name,exact:true})})
  let release!:()=>void;let arrived!:()=>void
  const gate = new Promise<void>(resolve=>{release=resolve});const started=new Promise<void>(resolve=>{arrived=resolve})
  await page.route(`**/api/quality-rules/${passing}/run`,async route=>{const response=await route.fetch();arrived();await gate;await route.fulfill({response})})
  await card('Error severity, passing rows').getByRole('button',{name:'Run check'}).click();await started
  await expect(page.getByRole('region',{name:'Quality execution status'}).getByText('running',{exact:true})).toBeVisible()
  release()
  const result=page.getByRole('region',{name:'Quality execution result'})
  await expect(result.getByText('passed',{exact:true})).toBeVisible()
  await expect(card('Error severity, passing rows').getByText('Severity: Error',{exact:true})).toBeVisible()
  const history=(await (await request.get(`/api/quality-rules/${passing}/runs`)).json())[0]
  expect(history.status).toBe('passed');expect(history.failed_rows).toBe(0);expect(history.evaluated_rows).toBe(3)
  await capture(page,'severity-error-execution-passed.png')
  await card('Warning severity, duplicate rows').getByRole('button',{name:'Run check'}).click()
  await expect(result.getByText('failed',{exact:true})).toBeVisible()
  await expect(card('Warning severity, duplicate rows').getByText('Severity: Warning',{exact:true})).toBeVisible()
  const failed = page.waitForResponse(r=>r.url().endsWith(`/api/quality-rules/${erroneous}/run`))
  await card('Actual execution conversion error').getByRole('button',{name:'Run check'}).click()
  expect((await failed).status()).toBe(400)
  const status = page.getByRole('region',{name:'Quality execution status'})
  await expect(status.getByText('error',{exact:true})).toBeVisible()
  await expect(status.getByText('Execution could not complete. No failed-row count was calculated.')).toBeVisible()
  await expect(result).toHaveCount(0)
  expect(await (await request.get(`/api/quality-rules/${erroneous}/runs`)).json()).toEqual([])
  await capture(page,'execution-error-no-failure-count.png')
  expect(errors).toEqual([])
})

test('a stale save returns conflict and preserves both the local draft and server versions', async ({page, request}) => {
  const project=(await (await request.post('/api/projects',{data:{name:'Concurrent draft regression'}})).json()).id
  const source=await upload(request,project,'source',Buffer.from('id\n1\n'))
  const definition={source_dataset_id:source,steps:[],pre_rule_ids:[],post_rule_ids:[],strict_quality:true,output:{format:'csv',filename:'v1.csv'}}
  const saved=(await (await request.post('/api/pipelines',{data:{project_id:project,name:'Concurrent pipeline',definition}})).json()).id
  await open(page,project,'Pipeline Builder');await selectPipeline(page,'Concurrent pipeline')
  const elsewhere=await request.put(`/api/pipelines/${saved}`,{data:{definition:{...definition,output:{format:'csv',filename:'v2.csv'}},expected_current_version:1}})
  expect(elsewhere.ok()).toBeTruthy()
  await page.getByLabel('Output filename').fill('local-draft.csv')
  const pending=page.waitForResponse(r=>r.url().endsWith(`/api/pipelines/${saved}`)&&r.request().method()==='PUT')
  await page.getByRole('button',{name:'Save version',exact:true}).click()
  expect((await pending).status()).toBe(409)
  await expect(page.getByRole('alert').getByText('Your draft remains in the editor.',{exact:false})).toBeVisible()
  await expect(page.getByLabel('Output filename')).toHaveValue('local-draft.csv')
  const server=await (await request.get(`/api/pipelines/${saved}`)).json()
  expect(server.current_version).toBe(2);expect(server.versions.length).toBe(2)
  expect(server.definition.output.filename).toBe('v2.csv')
  expect((await (await request.get(`/api/pipelines/${saved}?version=1`)).json()).definition.output.filename).toBe('v1.csv')
})

test('five pipeline records remain visible and executable after an actual server restart', async ({page, request}) => {
  test.skip(process.env.DATAGUARD_VERIFY_RESTART !== '1','Run through scripts/audit/pipeline_persistence.py for a real process restart')
  test.setTimeout(120000)
  const projects=await (await request.get('/api/projects')).json()
  const project=projects.find((p:{name:string})=>p.name==='Pipeline persistence regression').id
  await open(page,project,'Pipeline Builder')
  for(let index=1;index<=5;index++) {
    const name=`pipeline_test_0${index}`
    const records=await (await request.get(`/api/projects/${project}/pipelines`)).json()
    const pipeline=records.find((p:{name:string})=>p.name===name)
    await selectPipeline(page,name)
    expect((await (await request.get(`/api/pipelines/${pipeline.id}`)).json()).versions.length).toBe(index===1?3:1)
    await execute(page,request,pipeline.id,index===1?3:1,index)
    await page.locator('nav').getByRole('button',{name:'Pipeline Builder',exact:true}).click()
  }
  await capture(page,'five-pipelines-after-restart.png')
})
