# Contributing

AHM combines research software with a versioned measurement instrument. Contributions therefore require both engineering evidence and scientific traceability.

## Workflow

1. Open an issue describing the problem, affected specification section, and proposed evidence.
2. Keep infrastructure changes separate from scientific-semantic changes.
3. Add or update unit, adversarial, fixture, and golden tests as appropriate.
4. Run the offline checks documented in `README.md`.
5. Submit a focused pull request with compatibility and provenance notes.

Changes to feature meaning, relation values, opportunity sets, factual-target rules, thresholds, scoring equations, validation gates, or inference language require an explicit specification update or amendment and a new semantic version where applicable.

Generated raw runs belong in the artifact store described in `artifacts/README.md`. Small representative fixtures belong in Git when a test depends on them.

Please avoid committing credentials, `.env` files, private keys, internal endpoints, full external source documents, model weights, database dumps, or bulk model outputs.
