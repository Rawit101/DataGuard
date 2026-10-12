# API

Start DataGuard and open [FastAPI's interactive API documentation](http://127.0.0.1:8000/docs). Requests use JSON except `POST /api/imports/inspect`, which accepts multipart `file` and an `options` JSON string. Responses include validation errors. The app is loopback-only and does not implement remote authentication.

| Area | Endpoints |
| --- | --- |
| Health/settings | `GET /api/health`, `GET /api/settings` |
| Projects | `GET/POST /api/projects`, `PUT /api/projects/{id}` (rename), `GET /api/projects/{id}/overview` |
| Import/datasets | `POST /api/imports/inspect`, `POST /api/imports/commit`, `GET /api/projects/{id}/datasets`, `GET /api/datasets/{id}`, `GET /api/datasets/{id}/rows`, `GET /api/datasets/{id}/profile`, `GET /api/datasets/{id}/issues` |
| SQL | `POST /api/queries/run`, `GET/POST /api/projects/{id}/queries` or `/api/queries`, `PUT/DELETE /api/queries/{id}`, `POST /api/queries/export`, `GET /api/export-jobs/{id}`, `GET /api/projects/{id}/export-jobs`, `POST /api/export-jobs/{id}/cancel` |
| Quality | `GET /api/projects/{id}/quality-rules`, `POST /api/quality-rules`, `PUT/DELETE /api/quality-rules/{id}`, `POST /api/quality-rules/{id}/run`, `GET /api/quality-rules/{id}/runs` |
| Pipelines | `GET /api/projects/{id}/pipelines`, `POST /api/pipelines`, `GET/PUT /api/pipelines/{id}`, `POST /api/pipelines/{id}/duplicate`, `POST /api/pipelines/preview`, `POST /api/pipelines/script`, `POST /api/pipelines/{id}/run` |
| Runs/artifacts | `GET /api/projects/{id}/runs`, `GET /api/runs/{id}`, `POST /api/runs/{id}/cancel`, `POST /api/runs/{id}/rerun`, `GET /api/projects/{id}/artifacts`, `GET /api/artifacts/{id}/download` |
| Tutorial | `POST /api/demo/load` |

Typical flow: create a project, inspect and commit each upload, run quality checks, preview and save a pipeline, queue its run, poll `/api/runs/{id}`, then download the artifact ID returned on completion. Paging endpoints accept `page` and `page_size` (maximum 100). Dataset rows also accept `sort`, `direction`, `filter_column`, and `filter_text`. SQL and pipeline previews return bounded rows and full-result counts. Long-running full exports and pipeline runs return a job or run ID immediately.

The generated OpenAPI schema at `/openapi.json` is the endpoint and request-field reference. The frontend's `src/api.ts` contains its typed request helpers.
