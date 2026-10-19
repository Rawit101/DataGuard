import { useEffect, useRef, useState } from 'react'
import { Activity, Database, GitBranch, Home, Settings, ShieldCheck, SquareTerminal } from 'lucide-react'
import { api, type Project } from './api'
import { Overview } from './Overview'
import { Datasets } from './Datasets'
import { SqlWorkspace } from './SqlWorkspace'
import { Quality } from './Quality'
import { PipelineBuilder } from './PipelineBuilder'
import { Runs } from './Runs'
import { Help } from './Help'

const pages = [
  {id:'overview', label:'Overview', icon:Home, subtitle:'Your project at a glance'},
  {id:'datasets', label:'Datasets', icon:Database, subtitle:'Import, inspect, and profile local files'},
  {id:'sql', label:'SQL Workspace', icon:SquareTerminal, subtitle:'Query approved datasets safely'},
  {id:'quality', label:'Data Quality', icon:ShieldCheck, subtitle:'Define checks and inspect failures'},
  {id:'pipelines', label:'Pipeline Builder', icon:GitBranch, subtitle:'Build repeatable transformations'},
  {id:'runs', label:'Runs & Exports', icon:Activity, subtitle:'Track jobs and download results'},
  {id:'help', label:'Settings & Help', icon:Settings, subtitle:'Setup, limits, and a guided workflow'}
] as const
export type PageId = typeof pages[number]['id']
// Storage can be unavailable (blocked site data, some private modes); the app must still load.
function readStoredProject(){try{return localStorage.getItem('dataguard-project') || ''}catch{return ''}}
function storeProject(id:string){try{localStorage.setItem('dataguard-project',id)}catch{/* per-viewer convenience only */}}

export type PageProps = { projectId:string; refresh:()=>void; notify:(message:string, error?:boolean)=>void; navigate:(page:PageId)=>void }

export default function App(){
  const [page,setPage] = useState<PageId>('overview')
  const [projects,setProjects] = useState<Project[]>([])
  const [projectsLoaded,setProjectsLoaded] = useState(false)
  const [projectsError,setProjectsError] = useState('')
  const [projectId,setProjectId] = useState(readStoredProject)
  const [notice,setNotice] = useState<{message:string;error:boolean}|null>(null)
  const [refreshKey,setRefreshKey] = useState(0)
  const noticeTimer=useRef<number|undefined>(undefined)
  // Restart the timer so an older notice cannot dismiss a newer one early.
  const notify=(message:string,error=false)=>{setNotice({message,error});window.clearTimeout(noticeTimer.current);noticeTimer.current=window.setTimeout(()=>setNotice(null),7000)}
  useEffect(()=>()=>window.clearTimeout(noticeTimer.current),[])
  const refresh=()=>setRefreshKey(v=>v+1)
  useEffect(()=>{api<Project[]>('/projects').then(items=>{
    setProjects(items)
    setProjectId(current=>items.some(p=>p.id===current)?current:items[0]?.id || '')
    setProjectsError('')
    setProjectsLoaded(true)
  }).catch(e=>{setProjectsError(String(e));notify(String(e),true)})},[refreshKey])
  useEffect(()=>{storeProject(projectId)},[projectId])
  const current=pages.find(p=>p.id===page) || pages[0]
  const props:PageProps={projectId,refresh,notify,navigate:setPage}
  return <div className="app">
    <aside className="sidebar" aria-label="Primary navigation">
      <div className="brand"><span className="brand-icon"><ShieldCheck size={23}/></span>DataGuard</div>
      <nav className="nav">{pages.map(item=><button key={item.id} type="button" className={page===item.id?'active':''} onClick={()=>setPage(item.id)} aria-current={page===item.id?'page':undefined}><item.icon size={18}/>{item.label}</button>)}</nav>
      <div className="sidebar-footer">Local workspace · Private by default<br/>Version 0.1.0</div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="page-heading"><p className="eyebrow">Data engineering workspace</p><h1>{current.label}</h1><p>{current.subtitle}</p></div>
        <div className="field project-control"><label htmlFor="project-select">Project</label><select id="project-select" className="control" value={projectId} onChange={e=>setProjectId(e.target.value)}><option value="">Choose project</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
      </header>
      {notice&&<div role="status" className={'notice'+(notice.error?' error':'')}>{notice.message}</div>}
      {!projectsLoaded&&<div className="card" role="status">{projectsError?<>Could not load projects: {projectsError} <button className="btn small" onClick={refresh}>Retry</button></>:'Loading projects…'}</div>}
      {projectsLoaded&&page==='overview'&&<Overview {...props} selectProject={setProjectId}/>}
      {projectsLoaded&&page==='help'&&<Help {...props}/>}
      {projectsLoaded&&!projectId&&page!=='overview'&&page!=='help'&&<div className="card empty"><strong>Create a project to begin</strong>Use Overview to create a project or load the demo.</div>}
      {projectsLoaded&&projectId&&page==='datasets'&&<Datasets key={projectId} {...props}/>}
      {projectsLoaded&&projectId&&page==='sql'&&<SqlWorkspace key={projectId} {...props}/>}
      {projectsLoaded&&projectId&&page==='quality'&&<Quality key={projectId} {...props}/>}
      {projectsLoaded&&projectId&&page==='pipelines'&&<PipelineBuilder key={projectId} {...props}/>}
      {projectsLoaded&&projectId&&page==='runs'&&<Runs key={projectId} {...props}/>}
    </main>
  </div>
}
