# Live spec

`live.spec.ts` runs the thin slice against a stack that is already up (its edge proxy, the production build under the
real CSP, the real API, workers and models): paste, results, editor, validate, export (ZIP, SDF, CSV), report, golden
PDF, delete.

Opt-in. The default `npx playwright test` ignores this folder (`testIgnore` in `playwright.config.ts`) and CI never
runs it. The config refuses to load without `LIVE_BASE_URL`.

```sh
# Playwright image, from web/ (the same image as scripts/check.sh); golden/ is mounted for the PDF the spec uploads
nice -n 19 docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp \
  -e LIVE_BASE_URL=http://127.0.0.1:8090 -e LIVE_PDF=/golden/zou2024_penicillium_shentong.pdf \
  -v "$PWD":/work -v "$PWD/../golden":/golden:ro -w /work \
  mcr.microsoft.com/playwright:v1.64.1-noble npx playwright test --config e2e/live/live.config.ts

scripts/check.sh --live      # the same, after the other checks; LIVE_BASE_URL defaults to http://127.0.0.1:8090
npm run e2e:live             # with a local browser: LIVE_BASE_URL=... (LIVE_PDF defaults to ../golden/)
```

- One worker, one job at a time (about 2 minutes, nearly all of it the PDF job). It deletes the jobs it makes.
- The steps are serial and share one page: a failed step ends the run (the later ones need its state).
- Wall times per step are printed (`LIVE_TIMING ...`), kept in each test's annotations and in `test-results/live/report.json`.
  A failed step keeps a full-page screenshot, the console and API log (`console-and-api-log.txt`) and `trace.zip`
  in `test-results/live/`.
- What is recognised is the models' business: the spec compares the page with the job's own JSON and with the exports,
  and does not pin SMILES (it notes in the annotations whether result 1 equals the golden run's reading of the crop).
- `fixtures/00_crop-0.png` is `golden/runs/next-20261023-071544/crops/00_crop-0.png`.
