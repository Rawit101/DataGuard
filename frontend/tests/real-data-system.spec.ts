import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
import { execFileSync } from 'node:child_process'

const root=resolve('..')
const fixtures=join(root,'tests/fixtures/e2e')
const evidence=process.env.DATAGUARD_EVIDENCE_DIR
const engine=process.env.DATAGUARD_TEST_ENGINE||'chromium'
let events: Record<string,unknown>[], errors:string[]
test.beforeEach(async({page,request})=>{
  const settings=await (await request.get('/api/settings')).json()
  expect(settings.workspace).toBe(process.env.DATAGUARD_WORKSPACE||join(root,'work/playwright-workspace'))
  expect(settings.workspace).not.toContain('full-system-workspace')
  events=[];errors=[]
  page.on('response',r=>{if(r.url().includes('/api/'))events.push({method:r.request().method(),path:new URL(r.url()).pathname,status:r.status()})})
  page.on('requestfailed',r=>events.push({path:new URL(r.url()).pathname,failure:r.failure()?.errorText}))
  page.on('pageerror',e=>errors.push(e.message))
  page.on('console',m=>{if(m.type()==='error')events.push({console:m.text()})})
})
test.afterEach(async({page},info)=>{
  if(evidence&&info.status!==info.expectedStatus)await page.screenshot({path:join(evidence,`${engine}-failure.png`),fullPage:true})
  if(evidence){await mkdir(evidence,{recursive:true});await writeFile(join(evidence,`${engine}-${info.title.replace(/[^a-z0-9]+/gi,'-')}.json`),JSON.stringify({status:info.status,events,pageErrors:errors},null,2))}
  expect(errors).toEqual([])
})
async function capture(page:Page,name:string){if(evidence){await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:join(evidence,`${engine}-${name}.png`),fullPage:true})}}
async function navigate(page:Page,name:string){await page.locator('nav').getByRole('button',{name,exact:true}).click()}
async function open(page:Page,id:string,name:string){await page.goto('/');await page.getByLabel('Project',{exact:true}).selectOption(id);await navigate(page,name)}
async function json(request:APIRequestContext,path:string){const r=await request.get(path);expect(r.ok(),await r.text()).toBeTruthy();return r.json()}
async function createUI(page:Page,name:string){
  await navigate(page,'Overview');await page.getByLabel('New project name').fill(name)
  const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/projects'&&r.request().method()==='POST')
  await page.getByRole('button',{name:'Create project',exact:true}).click()
  const r=await pending;expect(r.ok()).toBeTruthy();const p=await r.json();await expect(page.getByLabel('Project',{exact:true})).toHaveValue(p.id);return p.id as string
}
async function uploadUI(page:Page,request:APIRequestContext,project:string,file:string,rows:number){
  await navigate(page,'Datasets');await page.getByLabel('CSV or Parquet file').setInputFiles(join(fixtures,file))
  await page.getByRole('button',{name:'Inspect file',exact:true}).click()
  await expect(page.getByText(new RegExp(`${rows.toLocaleString()} accepted rows`))).toBeVisible()
  const alias=file.replace('.csv','')
  await page.getByLabel('SQL alias (optional)').fill(alias)
  const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/imports/commit')
  await page.getByRole('button',{name:'Commit import',exact:true}).click()
  const r=await pending;expect(r.ok(),await r.text()).toBeTruthy();const ds=await r.json()
  await expect(page.getByRole('button',{name:`${alias} · ${rows} rows`,exact:true})).toBeVisible()
  expect((await json(request,`/api/projects/${project}/datasets`)).some((d:{id:string})=>d.id===ds.id)).toBeTruthy()
  return ds.id as string
}
async function queryUI(page:Page,sql:string,expected:unknown[]){
  await expect(page.getByRole('textbox',{name:'Query',exact:true})).toBeEnabled();await page.getByRole('textbox',{name:'Query',exact:true}).fill(sql)
  const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/queries/run')
  await page.getByRole('button',{name:'Run query',exact:true}).click()
  const r=await pending;expect(r.ok(),await r.text()).toBeTruthy();const result=await r.json();expect(result.rows).toEqual(expected)
  await expect(page.getByRole('heading',{name:'Results',exact:true})).toBeVisible();return result
}
async function decode(path:string){return JSON.parse(execFileSync(join(root,'.venv/bin/python'),[join(root,'scripts/audit/read_export.py'),path],{encoding:'utf8'}))}
async function exportUI(page:Page,format:string,name:string){
  await page.getByLabel('Export format').selectOption(format);await page.getByLabel('Filename',{exact:true}).fill(name)
  if(format==='csv')await page.getByLabel('UTF-8 BOM for spreadsheet apps').check()
  const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Export full result',exact:true}).click()
  const download=await pending;expect(download.suggestedFilename()).toBe(name)
  const path=evidence?join(evidence,`${engine}-${name}`):test.info().outputPath(name)
  await download.saveAs(path)
  if(format==='csv')expect((await readFile(path)).subarray(0,3)).toEqual(Buffer.from([239,187,191]))
  return {path,...await decode(path)}
}
async function ruleUI(page:Page,request:APIRequestContext,source:string,name:string,kind:string,configure:()=>Promise<void>,failed:number,severity='error'){
  await page.getByRole('button',{name:'New',exact:true}).click();await page.getByLabel('Dataset',{exact:true}).selectOption(source)
  await page.getByLabel('Rule name').fill(name);await page.getByLabel('Check',{exact:true}).selectOption(kind);await page.getByLabel('Severity',{exact:true}).selectOption(severity)
  await configure()
  const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/quality-rules'&&r.request().method()==='POST')
  await page.getByRole('button',{name:'Create rule',exact:true}).click();const r=await pending;expect(r.ok(),await r.text()).toBeTruthy();const id=(await r.json()).id
  const runPending=page.waitForResponse(r=>r.url().endsWith(`/api/quality-rules/${id}/run`))
  await page.locator('.step').filter({has:page.getByRole('button',{name,exact:true})}).getByRole('button',{name:'Run check',exact:true}).click()
  const run=await runPending;expect(run.ok(),await run.text()).toBeTruthy();const actual=await run.json()
  expect(actual.failed_rows).toBe(failed);expect(actual.status).toBe(failed?'failed':'passed')
  const section=page.getByRole('region',{name:'Quality execution result'})
  await expect(section.locator('.stat').nth(1)).toHaveText(String(failed))
  expect((await json(request,`/api/quality-rules/${id}/runs`))[0].failed_rows).toBe(failed)
  return {id,actual}
}

test('real CSV project ingestion SQL quality transformation exports and dashboard',async({page,request})=>{
  test.setTimeout(180000)
  const oracle=JSON.parse(await readFile(join(fixtures,'expected.json'),'utf8'))
  await page.goto('/')
  const project=await createUI(page,`Real CSV acceptance ${engine}`)
  await expect(page.getByText('No datasets yet',{exact:true})).toBeVisible()
  await capture(page,'empty-overview')
  await page.getByLabel('Project name',{exact:true}).fill(`Real CSV acceptance renamed ${engine}`)
  const rename=page.waitForResponse(r=>r.url().endsWith(`/api/projects/${project}`)&&r.request().method()==='PUT')
  await page.getByRole('button',{name:'Rename project',exact:true}).click();expect((await rename).ok()).toBeTruthy()
  await page.reload();await expect(page.getByLabel('Project',{exact:true})).toHaveValue(project)
  const second=await createUI(page,`Independent project ${engine}`);expect(second).not.toBe(project)
  await page.getByLabel('Project',{exact:true}).selectOption(project)
  const sources:Record<string,string>={}
  for(const [file,count] of [['customers.csv',6],['provinces.csv',3],['invalid_types.csv',4],['empty_dataset.csv',0],['duplicate_records.csv',5],['large_dataset.csv',50000]] as const)
    sources[file]=await uploadUI(page,request,project,file,count)
  const ds=await json(request,`/api/datasets/${sources['customers.csv']}`)
  expect(ds.schema.map((c:{name:string})=>c.name)).toEqual(['customer_id','name','province','amount'])
  expect(ds.schema.find((c:{name:string})=>c.name==='amount').declared_type).toBe('BIGINT')
  const rows=await json(request,`/api/datasets/${ds.id}/rows`)
  expect(rows.rows.map((r:Record<string,unknown>)=>{const copy={...r};delete copy.__source_row_id;return copy})).toEqual([
    {customer_id:'1',name:'Somchai',province:'Bangkok',amount:'1500'},
    {customer_id:'2',name:'Suda',province:'Chiang Mai',amount:'2500'},
    {customer_id:'3',name:'Anan',province:'Bangkok',amount:'1000'},
    {customer_id:'4',name:'Mali',province:null,amount:'3000'},
    {customer_id:'4',name:'Mali',province:null,amount:'3000'},
    {customer_id:'5',name:'Nida',province:'Phuket',amount:'2000'}])
  await page.getByRole('button',{name:'customers · 6 rows',exact:true}).click();await expect(page.getByText('Somchai',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Schema',exact:true}).click();await expect(page.getByRole('cell',{name:'BIGINT',exact:true}).first()).toBeVisible()
  await page.getByRole('button',{name:'Profile',exact:true}).click();const profile=await json(request,`/api/datasets/${ds.id}/profile`)
  expect(profile.columns.find((c:{name:string})=>c.name==='province').null_count).toBe(2)
  await expect(page.getByRole('cell',{name:'province',exact:true})).toBeVisible();await capture(page,'dataset-profile')
  await page.getByLabel('Project',{exact:true}).selectOption(second);await expect(page.getByText('No datasets yet',{exact:true})).toBeVisible()
  expect(await json(request,`/api/projects/${second}/datasets`)).toEqual([])
  await page.getByLabel('Project',{exact:true}).selectOption(project);await navigate(page,'SQL Workspace')
  const queries:[string,unknown[]][]=[
    ['SELECT COUNT(*) AS n FROM customers',[{n:oracle.row_count}]],
    ['SELECT SUM(CAST(amount AS BIGINT)) AS total, AVG(CAST(amount AS DOUBLE)) AS average, COUNT(DISTINCT customer_id) AS distinct_ids FROM customers',[{total:oracle.total_amount,average:oracle.total_amount/oracle.row_count,distinct_ids:oracle.distinct_customer_ids}]],
    ['SELECT name FROM customers WHERE CAST(amount AS DOUBLE)>1500 ORDER BY name LIMIT 4',[{name:'Mali'},{name:'Mali'},{name:'Nida'},{name:'Suda'}]],
    ['SELECT province,COUNT(*) AS n FROM customers GROUP BY province ORDER BY province NULLS FIRST',oracle.province_groups],
    ['SELECT c.name,p.region FROM customers c JOIN provinces p ON c.province=p.province ORDER BY c.customer_id',[{name:'Somchai',region:'Central'},{name:'Suda',region:'North'},{name:'Anan',region:'Central'},{name:'Nida',region:'South'}]],
    ['SELECT name FROM customers WHERE province=\'Bangkok\' ORDER BY name',[{name:'Anan'},{name:'Somchai'}]],
    ['SELECT name FROM customers WHERE customer_id=\'absent\'',[]],
    ['SELECT COUNT(*) AS n,SUM(CAST(amount AS BIGINT)) AS total FROM large_dataset',[{n:oracle.large.rows,total:oracle.large.sum_amount}]],
  ]
  for(const [sql,expected] of queries)await queryUI(page,sql,expected)
  await queryUI(page,'SELECT id,label,CAST(amount AS BIGINT) AS amount FROM large_dataset ORDER BY CAST(id AS BIGINT) DESC LIMIT 2',[{id:'50000',label:'ลูกค้า_50000',amount:150},{id:'49999',label:'ลูกค้า_49999',amount:149}])
  const csv=await exportUI(page,'csv','thai-results.csv');expect(csv.rows.map((r:{label:string})=>r.label)).toEqual(['ลูกค้า_50000','ลูกค้า_49999'])
  await queryUI(page,'SELECT customer_id,name,province,CAST(amount AS BIGINT) AS amount FROM customers ORDER BY __source_row_id',rows.rows.map((r:Record<string,unknown>)=>({customer_id:r.customer_id,name:r.name,province:r.province,amount:Number(r.amount)})))
  const csvAll=await exportUI(page,'csv','customers-full.csv');const parquet=await exportUI(page,'parquet','customers-full.parquet')
  expect(csvAll.rows).toEqual(rows.rows.map((r:Record<string,unknown>)=>({customer_id:r.customer_id,name:r.name,province:r.province||'',amount:r.amount})))
  expect(parquet.rows).toEqual(rows.rows.map((r:Record<string,unknown>)=>({customer_id:r.customer_id,name:r.name,province:r.province,amount:Number(r.amount)})))
  await capture(page,'sql-real-results')
  await page.getByLabel('Save as').fill('Customer totals');await page.getByRole('button',{name:'Save query',exact:true}).click();await expect(page.getByText('Query saved',{exact:true})).toBeVisible()
  await page.getByRole('textbox',{name:'Query',exact:true}).fill('SELECT COUNT(*) AS n FROM customers');await page.getByRole('button',{name:'Save version',exact:true}).click()
  await page.reload();await navigate(page,'SQL Workspace');await page.getByLabel('Saved query').selectOption({label:'Customer totals'});await expect(page.getByRole('textbox',{name:'Query',exact:true})).toHaveValue('SELECT COUNT(*) AS n FROM customers')
  await page.getByRole('button',{name:'Delete',exact:true}).click();await expect(page.getByText('Saved query deleted',{exact:true})).toBeVisible()
  await navigate(page,'Data Quality')
  const required=await ruleUI(page,request,ds.id,'Province required','required',async()=>{await page.getByLabel('Column',{exact:true}).selectOption('province')},2)
  expect(required.actual.evaluated_rows).toBe(6);expect(required.actual.examples.map((r:{__source_row_id:number})=>r.__source_row_id)).toEqual([4,5]);await expect(page.getByText('33.3%',{exact:true})).toBeVisible()
  const failureDownload=page.waitForEvent('download');await page.getByRole('link',{name:'Download all failures',exact:true}).click();const f=await failureDownload
  const failurePath=evidence?join(evidence,`${engine}-required-failures.csv`):(await f.path())!;if(evidence)await f.saveAs(failurePath)
  expect((await decode(failurePath)).rows.map((r:{name:string})=>r.name)).toEqual(['Mali','Mali'])
  const unique=await ruleUI(page,request,ds.id,'Customer duplicate occurrences','unique',async()=>{await page.getByLabel('Key columns, comma separated').fill('customer_id');await page.getByLabel('Duplicate counting').selectOption('beyond_first')},1)
  expect(unique.actual.examples[0].__source_row_id).toBe(5);await expect(page.getByText('16.7%',{exact:true})).toBeVisible();await capture(page,'quality-one-duplicate')
  await ruleUI(page,request,ds.id,'All duplicate group rows','unique',async()=>{await page.getByLabel('Key columns, comma separated').fill('customer_id')},2,'warning')
  const positive=await ruleUI(page,request,ds.id,'Positive amount','range',async()=>{await page.getByLabel('Column',{exact:true}).selectOption('amount');await page.getByLabel('Minimum',{exact:true}).fill('0.01')},0)
  await capture(page,'quality-passed-error-severity')
  await ruleUI(page,request,sources['invalid_types.csv'],'Parse amount','parse_valid',async()=>{await page.getByLabel('Column',{exact:true}).selectOption('amount')},1)
  await ruleUI(page,request,ds.id,'Province accepted','accepted',async()=>{await page.getByLabel('Column',{exact:true}).selectOption('province');await page.getByLabel('Accepted values, comma separated').fill('Bangkok,Phuket')},1)
  await ruleUI(page,request,ds.id,'Customer ID pattern','regex',async()=>{await page.getByLabel('Column',{exact:true}).selectOption('customer_id');await page.getByLabel('Full-match pattern').fill('[0-9]+')},0)
  await ruleUI(page,request,ds.id,'Province lookup','foreign_key',async()=>{await page.getByLabel('Column',{exact:true}).selectOption('province');await page.getByLabel('Reference dataset').selectOption(sources['provinces.csv']);await page.getByLabel('Reference column').selectOption('province')},0)
  await ruleUI(page,request,sources['empty_dataset.csv'],'Empty has no rows','row_count',async()=>{await page.getByLabel('Minimum rows').fill('1')},1)
  await page.reload();await navigate(page,'Data Quality');await page.getByRole('button',{name:'Customer duplicate occurrences',exact:true}).click()
  await expect(page.getByRole('heading',{name:'Stored result: Customer duplicate occurrences'})).toBeVisible();await expect(page.getByText('16.7%',{exact:true})).toBeVisible()
  // A disposable rule exercises real edit/delete; rules referenced by saved versions are preserved.
  await page.getByLabel('Rule name').fill('Renamed duplicate rule');await page.getByRole('button',{name:'Save changes',exact:true}).click();await expect(page.getByText('Quality rule saved',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Delete',exact:true}).click();await expect(page.getByText('Quality rule deleted',{exact:true})).toBeVisible()
  await navigate(page,'Pipeline Builder');await page.getByRole('button',{name:'New pipeline',exact:true}).click()
  await page.getByLabel('Name',{exact:true}).fill('Real customers cleaned joined');await page.getByLabel('Source dataset').selectOption(ds.id)
  await page.getByLabel('Output format').selectOption('csv');await page.getByLabel('Output filename').fill('cleaned-customers.csv')
  const steps=[
    {type:'cast',column:'amount',target_type:'DOUBLE',failure_policy:'error'},
    {type:'filter',conditions:[{column:'amount',op:'gt',value:1500}]},
    {type:'deduplicate',keys:['customer_id','name','province','amount'],order_by:'__source_row_id',keep:'first'},
    {type:'select',columns:['customer_id','name','province','amount']},
    {type:'join',reference_dataset_id:sources['provinces.csv'],join_type:'left',keys:[{left:'province',right:'province'}],prefix:'ref_',cardinality:'many_to_one'},
    {type:'rename',mapping:{name:'customer_name'}},
    {type:'sort',by:[{column:'customer_id',direction:'asc'}]},
  ]
  for(const step of steps){await page.getByLabel('Operation',{exact:true}).selectOption(step.type);await page.getByRole('button',{name:'Add step',exact:true}).click();await page.getByRole('textbox',{name:'Step configuration (JSON)',exact:true}).last().fill(JSON.stringify(step))}
  await page.getByRole('button',{name:'Preview',exact:true}).click();await expect(page.getByText('3 total rows · showing up to 50',{exact:true})).toBeVisible();await capture(page,'pipeline-real-preview')
  const save=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/pipelines'&&r.request().method()==='POST');await page.getByRole('button',{name:'Save pipeline',exact:true}).click();const pipeline=(await (await save).json()).id
  const runResponse=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/pipelines/${pipeline}/run`);await page.getByRole('button',{name:'Run saved version',exact:true}).click();const runId=(await (await runResponse).json()).id
  await expect.poll(async()=>(await json(request,`/api/runs/${runId}`)).status).toBe('completed')
  const run=await json(request,`/api/runs/${runId}`);expect(run.step.map((s:{row_count:number})=>s.row_count)).toEqual([6,4,3,3,3,3,3]);expect(run.started_at).toBeTruthy();expect(run.finished_at>=run.started_at).toBeTruthy()
  await expect(page.getByRole('link',{name:'Download output',exact:true})).toBeVisible({timeout:20000})
  const pipelineDownload=page.waitForEvent('download');await page.getByRole('link',{name:'Download output',exact:true}).click();const downloaded=await pipelineDownload
  const pipelinePath=evidence?join(evidence,`${engine}-cleaned-customers.csv`):(await downloaded.path())!;if(evidence)await downloaded.saveAs(pipelinePath)
  const expectedClean=oracle.cleaned_rows.map((r:Record<string,string>)=>({customer_id:r.customer_id,customer_name:r.name,province:r.province,amount:r.amount,ref_province:r.province,ref_region:({'Chiang Mai':'North',Phuket:'South'} as Record<string,string>)[r.province]||'',__source_row_id:String(rows.rows.find((x:Record<string,string>)=>x.customer_id===r.customer_id).__source_row_id),ref_source_row_id:({'Chiang Mai':'2',Phuket:'3'} as Record<string,string>)[r.province]||''}))
  const decoded=await decode(pipelinePath)
  expect(decoded.columns).toEqual(['__source_row_id','customer_id','customer_name','province','amount','ref_province','ref_region','ref_source_row_id'])
  expect(decoded.rows.map((r:Record<string,string>)=>({...r,amount:Number(r.amount)}))).toEqual(expectedClean.map((r:Record<string,string>)=>({...r,amount:Number(r.amount)})))
  await capture(page,'runs-real-output')
  const base=(await json(request,`/api/pipelines/${pipeline}`)).definition
  const gates=[]
  for(const stage of ['pre','post']){
    const definition={...base,[`${stage}_rule_ids`]:[required.id],strict_quality:true}
    const saved=await request.post('/api/pipelines',{data:{project_id:project,name:`Blocked ${stage}`,definition}});expect(saved.ok()).toBeTruthy()
    const queued=await request.post(`/api/pipelines/${(await saved.json()).id}/run`);const gate=(await queued.json()).id
    await expect.poll(async()=>(await json(request,`/api/runs/${gate}`)).status).toBe('failed')
    const state=await json(request,`/api/runs/${gate}`);expect(state.output_artifact_id).toBeNull();expect(state.error).toContain(`${stage==='pre'?'Pre':'Post'}-run error-severity`);gates.push(state)
  }
  const warning=await request.post('/api/quality-rules',{data:{project_id:project,dataset_id:ds.id,name:'Warning gate',kind:'required',severity:'warning',config:{column:'province'}}})
  const warningId=(await warning.json()).id
  const saved=await request.post('/api/pipelines',{data:{project_id:project,name:'Warning allowed',definition:{...base,pre_rule_ids:[warningId],post_rule_ids:[positive.id],strict_quality:true,output:{format:'parquet',filename:'cleaned.parquet'}}}})
  const warningRun=(await (await request.post(`/api/pipelines/${(await saved.json()).id}/run`)).json()).id
  await expect.poll(async()=>(await json(request,`/api/runs/${warningRun}`)).status).toBe('completed')
  const warningDetail=await json(request,`/api/runs/${warningRun}`);expect(warningDetail.quality.map((q:{status:string})=>q.status)).toEqual(['failed','passed'])
  const pqDownload=await request.get(`/api/artifacts/${warningDetail.output_artifact_id}/download`);const pqPath=join(evidence||'/tmp',`${engine}-cleaned.parquet`);await writeFile(pqPath,await pqDownload.body())
  expect((await decode(pqPath)).rows).toEqual(expectedClean.map((r:Record<string,string>)=>({...r,__source_row_id:Number(r.__source_row_id),ref_source_row_id:r.ref_source_row_id?Number(r.ref_source_row_id):null,amount:Number(r.amount),province:r.province||null,ref_province:r.ref_province||null,ref_region:r.ref_region||null})))
  const overview=await json(request,`/api/projects/${project}/overview`)
  await navigate(page,'Overview');await expect(page.locator('.stat-card .stat')).toHaveText([String(overview.datasets.length),String(overview.pipelines.length),String(overview.runs.length)])
  const table=page.locator('.table-wrap tbody');await expect(table.locator('tr')).toHaveCount(overview.runs.length)
  for(const item of overview.runs)await expect(table).toContainText(item.id.slice(0,10))
  await capture(page,'overview-reconciled')
  await page.getByLabel('Project',{exact:true}).selectOption(second);await expect(page.locator('.stat-card .stat')).toHaveText(['0','0','0']);await page.getByLabel('Project',{exact:true}).selectOption(project)
  await navigate(page,'Settings & Help');const settings=await json(request,'/api/settings');await expect(page.getByText(settings.workspace,{exact:true})).toBeVisible();await capture(page,'settings')
  const artifacts=await json(request,`/api/projects/${project}/artifacts`)
  if(evidence)await writeFile(join(evidence,`${engine}-real-data-state.json`),JSON.stringify({project,second,sources,pipeline,runId,warningRun,required:required.id,artifacts,gates,overview,oracle},null,2))
  expect(events.filter(e=>typeof e.status==='number'&&e.status>=400)).toEqual([])
  expect(events.filter(e=>e.console)).toEqual([])
})

test('real invalid imports SQL security and error feedback',async({page,request})=>{
  test.setTimeout(90000)
  const project=(await (await request.post('/api/projects',{data:{name:`Boundary checks ${engine}`}})).json()).id
  await open(page,project,'Datasets')
  for(const [file,expected] of [['unsupported.txt','Choose a CSV or Parquet file'],['zero_bytes.csv','Empty file'],['invalid_encoding.csv','could not be read using utf-8-sig']] as const){
    await page.getByLabel('CSV or Parquet file').setInputFiles(join(fixtures,file));const pending=page.waitForResponse(r=>r.url().endsWith('/api/imports/inspect'))
    await page.getByRole('button',{name:'Inspect file',exact:true}).click();const response=await pending;expect(response.status()).toBe(400);expect(await response.text()).toContain(expected)
    await expect(page.getByRole('status').filter({hasText:expected})).toBeVisible()
  }
  const malformed=await uploadUI(page,request,project,'malformed.csv',2)
  const issues=await json(request,`/api/datasets/${malformed}/issues`);expect(issues.total_rows).toBe(2);expect(issues.rows[0].reason).toContain('Expected 2 columns');expect(issues.rows[1].raw_line).toContain('tail preserved')
  await page.getByRole('button',{name:'Import issues (2)',exact:true}).click();await expect(page.getByRole('cell',{name:/record boundaries are uncertain/})).toBeVisible();await capture(page,'malformed-retained')
  await uploadUI(page,request,project,'customers.csv',6);await navigate(page,'SQL Workspace')
  for(const sql of ['SELECT FROM','SELECT * FROM unknown_table','SELECT unknown_column FROM customers','DELETE FROM customers',"SELECT * FROM read_csv('/etc/passwd')",'SELECT * FROM customers; DROP TABLE customers']){
    await expect(page.getByRole('textbox',{name:'Query',exact:true})).toBeEnabled();await page.getByRole('textbox',{name:'Query',exact:true}).fill(sql)
    const pending=page.waitForResponse(r=>r.url().endsWith('/api/queries/run'));await page.getByRole('button',{name:'Run query',exact:true}).click();const response=await pending
    expect(response.status()).toBe(400);await expect(page.getByRole('alert')).toBeVisible()
    expect((await json(request,`/api/projects/${project}/datasets`)).length).toBe(2)
  }
  await capture(page,'sql-rejected')
  const settings=await json(request,'/api/settings')
  const tooLarge=await request.post('/api/imports/inspect',{multipart:{file:{name:'oversize.csv',mimeType:'text/csv',buffer:Buffer.alloc(settings.max_upload_bytes+1,65)},options:'{}'}})
  expect(tooLarge.status()).toBe(400);expect(await tooLarge.text()).toContain('upload limit')
  for(const token of ['../metadata.sqlite3','/etc/passwd','..%2fmetadata.sqlite3']){
    const r=await request.post('/api/imports/commit',{data:{project_id:project,name:'No traversal',token}});expect([400,404]).toContain(r.status())
  }
  const badOrigin=await request.post('/api/projects',{headers:{Origin:'https://attacker.invalid'},data:{name:'Blocked'}});expect(badOrigin.status()).toBe(403)
  const badHost=await request.get('/api/settings',{headers:{Host:'attacker.invalid'}});expect(badHost.status()).toBe(400)
  const datasets=await json(request,`/api/projects/${project}/datasets`)
  const other=(await (await request.post('/api/projects',{data:{name:'Other boundary project'}})).json()).id
  const crossRule=await request.post('/api/quality-rules',{data:{project_id:other,dataset_id:datasets[0].id,name:'Cross project',kind:'required',config:{column:'id'}}});expect(crossRule.status()).toBe(404)
  const foreignSQL=await request.post('/api/queries/run',{data:{project_id:other,sql:'SELECT * FROM customers'}});expect(foreignSQL.status()).toBe(400)
  const invalidRule=await request.post('/api/quality-rules',{data:{project_id:project,dataset_id:datasets[0].id,name:'Invalid',kind:'range',config:{column:'missing',min:5,max:1}}});expect(invalidRule.status()).toBe(400)
  const traversal=await request.post('/api/queries/export',{data:{project_id:project,sql:'SELECT * FROM customers',name:'../../outside.csv',format:'csv'}});expect(traversal.status()).toBe(400)
  const longSQL=await request.post('/api/queries/run',{data:{project_id:project,sql:'SELECT '+ '1'.repeat(50000)}});expect(longSQL.status()).toBe(400)
  const largeStage=await request.post('/api/imports/inspect',{multipart:{file:{name:'large_dataset.csv',mimeType:'text/csv',buffer:await readFile(join(fixtures,'large_dataset.csv'))},options:'{}'}})
  expect(largeStage.ok()).toBeTruthy()
  const largeCommit=await request.post('/api/imports/commit',{data:{project_id:project,token:(await largeStage.json()).token,name:'large_dataset',alias:'large_dataset',type_overrides:{}}});expect(largeCommit.ok()).toBeTruthy()
  const timeout=await request.post('/api/queries/run',{data:{project_id:project,sql:'SELECT SUM(LENGTH(CONCAT(a.label,b.label))) FROM large_dataset a CROSS JOIN large_dataset b'}})
  expect(timeout.status()).toBe(400);expect(await timeout.text()).toMatch(/interrupt/i)
  const secretPattern=/sk-[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/
  for(const path of ['/api/settings','/api/projects',`/api/projects/${project}/datasets`])expect(await (await request.get(path)).text()).not.toMatch(secretPattern)
})

test('actual network unavailability shows retry and recovers',async({page,context,request})=>{
  const project=(await (await request.post('/api/projects',{data:{name:`Retry ${engine}`}})).json()).id
  await open(page,project,'Overview');await expect(page.locator('.stat-card')).toHaveCount(3)
  await context.setOffline(true);await navigate(page,'Settings & Help');await expect(page.getByRole('button',{name:'Retry settings',exact:true})).toBeVisible()
  await capture(page,'offline-error')
  await context.setOffline(false);await page.getByRole('button',{name:'Retry settings',exact:true}).click();await expect(page.getByText('Storage location',{exact:true})).toBeVisible()
  await navigate(page,'SQL Workspace');await expect(page.getByText('Import a dataset first.',{exact:true})).toBeVisible()
  await navigate(page,'Pipeline Builder');await expect(page.getByRole('button',{name:'Save pipeline',exact:true})).toBeDisabled()
  await navigate(page,'Runs & Exports');await expect(page.getByText('No runs yet',{exact:true})).toBeVisible()
})

test('real data projects history exports and settings survive process restart',async({page,request})=>{
  test.skip(process.env.DATAGUARD_VERIFY_RESTART!=='1','Controlled driver restarts the real server before this test')
  test.setTimeout(60000)
  const state=JSON.parse(await readFile(join(evidence!,`${engine}-real-data-state.json`),'utf8'))
  const settings=await json(request,'/api/settings');expect(settings.workspace).toBe(process.env.DATAGUARD_WORKSPACE||join(root,'work/playwright-workspace'))
  await open(page,state.project,'Overview');await expect(page.getByLabel('Project name',{exact:true})).toHaveValue(`Real CSV acceptance renamed ${engine}`)
  expect(await json(request,`/api/projects/${state.project}/overview`)).toEqual(state.overview)
  await navigate(page,'Data Quality');await page.getByRole('button',{name:'Province required',exact:true}).click();await expect(page.getByRole('heading',{name:'Stored result: Province required'})).toBeVisible();await expect(page.getByText('33.3%',{exact:true})).toBeVisible()
  await navigate(page,'Runs & Exports');await expect(page.getByRole('button',{name:'Real customers cleaned joined',exact:true})).toBeVisible()
  for(const artifact of state.artifacts){const response=await request.get(`/api/artifacts/${artifact.id}/download`);expect(response.ok()).toBeTruthy();expect((await response.body()).length).toBeGreaterThan(0)}
  const r=await json(request,`/api/runs/${state.runId}`);expect(r.status).toBe('completed');expect(r.step.map((s:{row_count:number})=>s.row_count)).toEqual([6,4,3,3,3,3,3])
  expect(await json(request,`/api/projects/${state.second}/datasets`)).toEqual([])
  await capture(page,'after-restart-history')
})
