# ExitCapsule

**AI-powered dependency resilience engine** — Track 1 (Best Use of Gemma 4), *Building Resilient Open-Source Software with AI*.

ExitCapsule analyses any Python repository, finds at-risk dependencies from evidence (manifests, AST usage, PyPI/GitHub metadata), asks **Gemma 4** to propose replacements and patches, **executes** the repo's tests against the original and the replacement, and packages everything into a reusable **Dependency Exit Capsule** — with provenance (`OBSERVED` / `EXECUTED` / `AI`) on every claim.

> Status: work in progress — see [`docs/STATUS.md`](docs/STATUS.md).

## Requirement traceability

| Brief requirement | Engine feature | Provenance |
|---|---|---|
| 1. Identify at-risk deps | manifests + PyPI/GitHub metadata + deterministic risk signals | observed |
| 2. Usage locations | AST usage scanner (file:line:symbol:snippet) | observed |
| 3. Replacement + migration | Gemma proposals, verified against PyPI, plus a Gemma patch | ai → verified/failed |
| 4. Execute tests old vs new | uv envs, import/CLI smoke, pytest, differential characterization tests | executed |
| 5. Compatibility gaps | per-test transitions, warnings diff, untested usage sites | executed + ai explanation |
| 6. Exit Capsule | JSON (schema) + MD + HTML + patch + replay scripts | all, tagged |

## Gemma 4

Model: `gemma-4-31b-it` (auto-discovered via the Gemini API model list; never falls back to a non-Gemma model). Access: Google AI Studio / Gemini API through the `google-genai` SDK; Ollama optional. Details and terms: [`docs/GEMMA.md`](docs/GEMMA.md).

## Quick start

Windows (PowerShell):

```powershell
git clone https://github.com/goelavi04/exitcapsule; cd exitcapsule
./scripts/setup.ps1          # installs tools, uv sync, creates .env, runs doctor
# edit .env -> GEMINI_API_KEY=<your key>
uv run exitcapsule doctor
uv run exitcapsule analyze https://github.com/<owner>/<repo>
```

Linux/macOS:

```bash
git clone https://github.com/goelavi04/exitcapsule && cd exitcapsule
./scripts/setup.sh
uv run exitcapsule analyze https://github.com/<owner>/<repo>
```

## License

MIT — see [LICENSE](LICENSE).
