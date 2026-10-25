# DECIMER.ai

Next-generation stack for [decimer.ai](https://decimer.ai): optical chemical structure recognition in the browser — segmentation of chemical structures from documents and their translation to machine-readable formats.

> Work in progress. This repository will replace [Steinbeck-Lab/DECIMER.ai](https://github.com/Steinbeck-Lab/DECIMER.ai) (Laravel 8), which will be archived as `DECIMER.ai_old`.

## Fresh checkout

Not in git, recreated locally: `web/node_modules`, every `.venv`, the model weights and `deploy/.env` (secrets).

- Tools: [uv](https://docs.astral.sh/uv/), Node 24, Docker.
- Python projects (`api`, `services/common`, `services/seg`, `services/ocsr`, `services/cls`, `services/chem`, `golden`): `uv sync --locked` in each. chem also needs a Java 21 runtime (`JAVA_HOME`).
- Web: `cd web && npm ci`.
- Checks: `scripts/check.sh` (`--no-e2e` skips the Docker-based end-to-end and browser tests).
- Weights for the model images: `deploy/weights/fetch.sh --zenodo` (verified against `deploy/weights/SHA256SUMS`).
- Running the stack: `deploy/README.md` (including how to create `deploy/.env` from `deploy/.env.example`) and `docs/RUNBOOK.md`. The images are built and tested on linux/amd64.
- Where things stand: `docs/HANDOFF.md`, `docs/TODO.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`.
