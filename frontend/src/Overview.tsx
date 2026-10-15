import { useEffect, useState } from 'react'
import { ArrowRight, Database, GitBranch, Play, Plus, Pencil } from 'lucide-react'
import { api, post, put } from './api'
import type { PageProps } from './App'

type OverviewData = {
  project: {id:string;name:string}
  datasets: {id:string;name:string;row_count:number;reject_count:number}[]
  pipelines: {id:string;name:string}[]
  runs: {id:string;status:string;created_at:string}[]
}

export function Overview({projectId,refresh,notify,navigate,selectProject}:PageProps&{selectProject:(id:string)=>void}){
  const [data,setData]=useState<OverviewData|null>(null)
  const [name,setName]=useState('')
  const [rename,setRename]=useState('')
  const [busy,setBusy]=useState(false)
  const [loading,setLoading]=useState(false)
  const [loadError,setLoadError]=useState('')
  const [reload,setReload]=useState(0)
  useEffect(()=>{let active=true;setData(null);setLoadError('');setLoading(Boolean(projectId));if(projectId)api<OverviewData>(`/projects/${projectId}/overview`).then(result=>{if(active){setData(result);setRename(result.project.name)}}).catch(e=>{if(active){setLoadError(String(e));notify(String(e),true)}}).finally(()=>{if(active)setLoading(false)});return()=>{active=false}},[projectId,reload])
  async function create(){if(!name.trim())return;setBusy(true);try{const p=await post<{id:string}>('/projects',{name:name.trim()});selectProject(p.id);setName('');refresh();notify('Project created')}catch(e){notify(String(e),true)}finally{setBusy(false)}}
  async function demo(){setBusy(true);try{const result=await post<{project_id:string}>('/demo/load',{});selectProject(result.project_id);refresh();notify('Sample project loaded with datasets, quality rules, and a pipeline')}catch(e){notify(String(e),true)}finally{setBusy(false)}}
  async function renameProject(){if(!projectId||!rename.trim())return;setBusy(true);try{const result=await put<{name:string}>(`/projects/${projectId}`,{name:rename.trim()});setData(old=>old?{...old,project:{...old.project,name:result.name}}:old);refresh();notify('Project renamed')}catch(e){notify(String(e),true)}finally{setBusy(false)}}
  return <>
    <div className="overview-project-grid">
      <section className="card">
        <div className="card-header">
          <div><h2>Start with your data</h2><p className="card-subtitle">Create a project for your files, or open the guided real estate example.</p></div>
          <button className="btn primary" onClick={demo} disabled={busy}><Play/>Load sample project</button>
        </div>
        <div className="form-row">
          <div className="field"><label htmlFor="new-project">New project name</label><input id="new-project" value={name} onChange={e=>setName(e.target.value)} placeholder="My data project" onKeyDown={e=>{if(e.key==='Enter')void create()}}/></div>
          <button className="btn" onClick={create} disabled={busy||!name.trim()}><Plus/>Create project</button>
        </div>
      </section>
      {data&&<section className="card">
        <div className="card-header"><div><h2>Current project</h2><p className="card-subtitle">Keep the workspace name clear for everyone using this computer.</p></div></div>
        <div className="form-row">
          <div className="field"><label htmlFor="rename-project">Project name</label><input id="rename-project" value={rename} onChange={e=>setRename(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')void renameProject()}}/></div>
          <button className="btn" onClick={renameProject} disabled={busy||!rename.trim()||rename.trim()===data.project.name}><Pencil/>Rename project</button>
        </div>
      </section>}
    </div>
    {data?<>
      <div className="stats-grid">
        <div className="card stat-card"><div className="stat">{data.datasets.length}</div><div className="stat-label">Datasets</div></div>
        <div className="card stat-card"><div className="stat">{data.pipelines.length}</div><div className="stat-label">Pipelines</div></div>
        <div className="card stat-card"><div className="stat">{data.runs.length}</div><div className="stat-label">Recent runs</div></div>
      </div>
      <div className="overview-panels">
        <section className="card overview-panel">
          <div className="card-header"><h2>Datasets</h2><button className="btn small" onClick={()=>navigate('datasets')}>Open <ArrowRight/></button></div>
          {data.datasets.length?<div className="overview-list">{data.datasets.map(d=><div className="overview-list-row" key={d.id}><span className="overview-list-name"><Database/>{d.name}</span><span className="small-text muted">{d.row_count.toLocaleString()} rows</span></div>)}</div>:<div className="empty"><strong>No datasets yet</strong>Import a CSV or Parquet file.</div>}
        </section>
        <section className="card overview-panel">
          <div className="card-header"><h2>Pipelines</h2><button className="btn small" onClick={()=>navigate('pipelines')}>Open <ArrowRight/></button></div>
          {data.pipelines.length?<div className="overview-list">{data.pipelines.map(p=><div className="overview-list-row" key={p.id}><span className="overview-list-name"><GitBranch/>{p.name}</span></div>)}</div>:<div className="empty"><strong>No pipelines yet</strong>Build a reusable pipeline after importing data.</div>}
        </section>
      </div>
      <section className="card">
        <div className="card-header"><h2>Recent runs</h2><button className="btn small" onClick={()=>navigate('runs')}>View runs <ArrowRight/></button></div>
        {data.runs.length?<div className="table-wrap"><table><thead><tr><th>Run</th><th>Status</th><th>Created</th></tr></thead><tbody>{data.runs.map(r=><tr key={r.id}><td className="mono">{r.id.slice(0,10)}</td><td><span className={'pill '+r.status}>{r.status}</span></td><td>{new Date(r.created_at).toLocaleString()}</td></tr>)}</tbody></table></div>:<div className="empty"><strong>No runs yet</strong>Load the sample pipeline and run it.</div>}
      </section>
    </>:loading?<div className="card" role="status">Loading project overview…</div>:loadError?<div className="card notice error" role="alert">{loadError} <button className="btn small" onClick={()=>setReload(v=>v+1)}>Retry overview</button></div>:<div className="card empty"><strong>No project selected</strong>Create a project above to start the full workflow.</div>}
  </>
}
