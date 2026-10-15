export type Project = { id: string; name: string; created_at: string }
export type Column = { name: string; inferred_type: string; declared_type: string }
export type Dataset = { id: string; project_id: string; name: string; alias: string; original_name: string; row_count: number; reject_count: number; size: number; sha256: string; schema: Column[]; created_at: string }
export type Rule = { id: string; project_id: string; dataset_id: string; name: string; kind: string; severity: string; config: Record<string, unknown>; created_at: string }
export type Pipeline = { id: string; project_id: string; name: string; current_version: number; selected_version?:number; created_at: string; updated_at: string; definition?: PipelineDefinition; versions?: {version:number;created_at:string}[] }
export type PipelineDefinition = { source_dataset_id: string; steps: Record<string, unknown>[]; pre_rule_ids: string[]; post_rule_ids: string[]; strict_quality: boolean; output: {format: string; filename: string; utf8_bom?: boolean} }
export type Run = { id:string; pipeline_id:string; pipeline_name?:string; version:number; status:string; cancel_requested:number; created_at:string; started_at?:string; finished_at?:string; duration_ms?:number; error?:string; output_artifact_id?:string; step?: Record<string, unknown>[]; quality?: Record<string, unknown>[]; lineage?:Record<string,unknown> }
export type Artifact = { id:string; project_id:string; run_id?:string; kind:string; format:string; name:string; row_count:number; created_at:string }
export type Page<T> = { rows:T[]; total_rows:number; page:number; page_size:number }

export async function api<T>(path:string, options:RequestInit = {}):Promise<T> {
  const response = await fetch('/api' + path, {
    ...options,
    headers: options.body instanceof FormData ? options.headers : { 'Content-Type':'application/json', ...options.headers }
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `${response.status} ${response.statusText}`)
  }
  return await response.json() as T
}

export const post = <T>(path:string, payload:unknown, headers?:HeadersInit) => api<T>(path, {method:'POST', body:JSON.stringify(payload), headers})
export const put = <T>(path:string, payload:unknown) => api<T>(path, {method:'PUT', body:JSON.stringify(payload)})
export const del = <T>(path:string) => api<T>(path, {method:'DELETE'})
