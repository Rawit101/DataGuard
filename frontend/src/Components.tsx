import type { ReactNode } from 'react'

export function DataTable({rows,empty='No rows to show'}:{rows:Record<string,unknown>[];empty?:string}){
  if(!rows.length)return <div className="empty"><strong>{empty}</strong></div>
  const columns=Object.keys(rows[0])
  return <div className="table-wrap"><table><thead><tr>{columns.map(c=><th key={c}>{c}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={i}>{columns.map(c=><td key={c}>{renderValue(row[c])}</td>)}</tr>)}</tbody></table></div>
}

function renderValue(value:unknown):ReactNode{
  if(value===null||value===undefined)return <span className="muted">NULL</span>
  if(typeof value==='object')return <span className="mono">{JSON.stringify(value)}</span>
  return String(value)
}

export function Pager({page,total,pageSize,onPage}:{page:number;total:number;pageSize:number;onPage:(page:number)=>void}){
  const pages=Math.max(1,Math.ceil(total/pageSize))
  return <div className="spread" style={{marginTop:12}}><span className="small-text muted">{total.toLocaleString()} rows · page {page} of {pages}</span><div className="row"><button className="btn small" onClick={()=>onPage(page-1)} disabled={page<=1}>Previous</button><button className="btn small" onClick={()=>onPage(page+1)} disabled={page>=pages}>Next</button></div></div>
}
