# Frontend build results

## What was run in the build environment
| Step | Result |
|---|---|
| TypeScript check (`tsc --noEmit`, strict, noUnusedLocals/Parameters) against a local React type shim | **0 errors** in application code (the shim cannot type the `./styles.css` side-effect import; Vite's `vite/client` types cover it in the real build) |
| Production bundle with esbuild (React 19 from the environment) | Built `dist/assets/index.js` + `index.css`; served by Flask |
| Playwright E2E, desktop 1440×900 | 19/19 checks passed (see API_TEST_RESULTS.md) |
| Mobile 390×844 | No horizontal scroll on cheque detail, verify, enrollment, training, history, settings; drawer menu works |
| Browser console | No errors besides the intentional failed-login 401 |

## What was NOT run here (please run on your machine)
The package registry was unreachable, so `npm install`, Vite and the official `@types/react` could not
be used. Run:

```powershell
cd Frontend
npm install
npm run build      # = tsc --noEmit && vite build -> Frontend/dist
```
`.\start.ps1 -Test` runs this together with the backend tests. The bundled `Frontend/dist` (not committed to git) is the esbuild
output from this environment and works as-is; `npm run build` replaces it with the Vite build.
If `tsc` reports an error with the real React 18 types, it will be in a typing detail, not in runtime
behaviour (the same code ran in Chromium); please send the message and it can be fixed quickly.

## Screens verified in the browser
Login · Dashboard · Cheque verification (upload, list, search, filters) · Cheque detail (fields, corrections,
validation, quality, signature crop, region adjustment, PDF, re-run OCR, delete) · Signature verification
(from cheque / upload, preprocessing preview, signer picker, result stamp, score scale, reasons,
feature table, approve/reject) · Signature enrollment (list, create, dataset import) · Signer detail
(specimens, replace/deactivate/delete, history, edit) · Batch processing · OCR dataset · Signature dataset ·
Model training (readiness, run jobs, history, results) · Analytics · Verification history · Reports · Settings
(thresholds, password, users, audit log).
