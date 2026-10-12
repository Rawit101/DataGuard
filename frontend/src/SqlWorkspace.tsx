import { useEffect, useState } from 'react'
import { Download, Play, Save, Trash2 } from 'lucide-react'
import { api, del, post, put, type Dataset } from './api'
import type { PageProps } from './App'
import { DataTable, Pager } from './Components'

type Saved = {id:string;name:string;sql_text:string;updated_at:string}
type QueryResult = {rows:Record<string,unknown>[];total_rows:number;page:number;page_size:number;duration_ms:number;sql:string}
export function SqlWorkspace({projectId,notify}:PageProps){
  const [datasets,setDatasets]=useState<Dataset[]>([])
  const [saved,setSaved]=useState<Saved[]>([])
  const [selected,setSelected]=useState('')
  const [name,setName]=useState('')
  const [sql,setSql]=useState('')
  const [result,setResult]=useState<QueryResult|null>(null)
  const [page,setPage]=useState(1)
  const [busy,setBusy]=useState(false)
  const [format,setFormat]=useState('parquet')
  const [filename,setFilename]=useState('query_result.parquet')
  const [bom,setBom]=useState(false)
  const [exportJob,setExportJob]=useState('')
  const refresh=()=>api<Saved[]>(`/projects/${projectId}/queries`).then(setSaved).catch(e=>notify(String(e),true))
  useEffect(()=>{api<Dataset[]>(`/projects/${projectId}/datasets`).then(items=>{setDatasets(items);setSql(items.length?`SELECT * FROM ${items[0].alias} LIMIT 50`:'');setResult(null)}).catch(e=>notify(String(e),true));refresh()},[projectId])
  async function run(targetPage=1){setBusy(true);setResult(null);try{const r=await post<QueryResult>('/queries/run',{project_id:projectId,sql,page:targetPage,page_size:50});setResult(r);setPage(targetPage);notify('Query completed')}catch(e){notify(String(e),true)}finally{setBusy(false)}}
  async function save(){setBusy(true);try{if(selected)await put(`/queries/${selected}`,{project_id:projectId,name,sql});else{const r=await post<{id:string}>('/queries',{project_id:projectId,name,sql});setSelected(r.id)}await refresh();notify('Query saved')}catch(e){notify(String(e),true)}finally{setBusy(false)}}
  async function remove(){if(!selected)return;setBusy(true);try{await del(`/queries/${selected}`);setSelected('');setName('');await refresh();notify('Saved query deleted')}catch(e){notify(String(e),true)}finally{setBusy(false)}}
  async function exportResult(){setBusy(true);try{const job=await post<{id:string}>('/queries/export',{project_id:projectId,sql,name:filename,format,utf8_bom:bom},{'Idempotency-Key':crypto.randomUUID()});setExportJob(job.id);notify('Full export queued in the background');let finished=false;for(let i=0;i<120;i++){await new Promise(resolve=>setTimeout(resolve,500));const state=await api<{status:string;artifact_id?:string;error?:string}>(`/export-jobs/${job.id}`);if(state.status==='completed'&&state.artifact_id){notify('Full query result exported');window.location.href=`/api/artifacts/${state.artifact_id}/download`;finished=true;break}if(['failed','cancelled','interrupted'].includes(state.status))throw new Error(state.error||`Export ${state.status}`)}if(!finished)notify('Export is still running. Track it under Runs & Exports.')}catch(e){notify(String(e),true)}finally{setBusy(false);setExportJob('')}}
  async function cancelExport(){if(!exportJob)return;try{const r=await post<{cancellation_requested:boolean}>(`/export-jobs/${exportJob}/cancel`,{});notify(r.cancellation_requested?'Export cancellation requested':'Export has already finished')}catch(e){notify(String(e),true)}}
  return <div className="grid" style={{gridTemplateColumns:'minmax(230px,300px) minmax(0,1fr)'}}>
    <div><section className="card"><h2>Available datasets</h2>{datasets.map(d=><div key={d.id} style={{padding:'8px 0',borderBottom:'1px solid #e9f0ed'}}><strong className="mono">{d.alias}</strong><div className="small-text muted">{d.row_count.toLocaleString()} rows · {d.schema.map(c=>c.name).join(', ')}</div></div>)}{!datasets.length&&<p className="muted">Import a dataset first.</p>}</section><section className="card"><h2>Saved queries</h2><select className="control" aria-label="Saved query" value={selected} onChange={e=>{const item=saved.find(q=>q.id===e.target.value);setSelected(e.target.value);setResult(null);if(item){setName(item.name);setSql(item.sql_text)}else setName('')}}><option value="">New query</option>{saved.map(q=><option key={q.id} value={q.id}>{q.name}</option>)}</select><div className="row" style={{marginTop:10}}><button className="btn small danger" onClick={remove} disabled={!selected||busy}><Trash2 size={14}/>Delete</button></div></section></div>
    <div><section className="card"><div className="spread"><h2>SQL editor</h2><span className="pill">Read only</span></div><p className="small-text muted">SELECT and CTE queries can reference only the aliases at left. File reads, writes, extensions, and unapproved functions are blocked.</p><label className="field" htmlFor="sql-editor"><span>Query</span><textarea id="sql-editor" className="control mono" style={{minHeight:210}} value={sql} onChange={e=>{setSql(e.target.value);setResult(null)}}/></label><div className="row" style={{marginTop:12}}><button className="btn primary" disabled={busy||!datasets.length||!sql.trim()} onClick={()=>run(1)}><Play size={16}/>Run query</button><div className="field"><label htmlFor="query-name">Save as</label><input id="query-name" value={name} onChange={e=>setName(e.target.value)} placeholder="Query name"/></div><button className="btn" disabled={busy||!name.trim()} onClick={save}><Save size={16}/>{selected?'Save version':'Save query'}</button></div></section>
      {result&&<section className="card"><div className="spread"><h2>Results</h2><span className="small-text muted">{result.total_rows.toLocaleString()} total rows · {result.duration_ms} ms · 50 per page</span></div><DataTable rows={result.rows}/><Pager page={page} total={result.total_rows} pageSize={50} onPage={run}/><div className="row" style={{marginTop:15}}><div className="field"><label htmlFor="export-format">Export format</label><select id="export-format" value={format} onChange={e=>{setFormat(e.target.value);setFilename(`query_result.${e.target.value}`)}}><option value="parquet">Parquet</option><option value="csv">CSV</option></select></div><div className="field"><label htmlFor="export-name">Filename</label><input id="export-name" value={filename} onChange={e=>setFilename(e.target.value)}/></div>{format==='csv'&&<label className="row small-text"><input type="checkbox" checked={bom} onChange={e=>setBom(e.target.checked)}/>UTF-8 BOM for spreadsheet apps</label>}<button className="btn" onClick={exportResult} disabled={busy}><Download size={16}/>Export full result</button>{exportJob&&<button className="btn danger" onClick={cancelExport}>Cancel export</button>}</div></section>}
    </div>
  </div>
}
