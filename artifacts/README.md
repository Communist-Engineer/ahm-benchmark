# Artifact policy

Ordinary Git contains compact prompt data, adversarial and golden fixtures, schemas, manifests, and source provenance.

Bulk raw runs, judge outputs, database dumps, third-party factual-source documents, model weights, caches, and large derived datasets belong in a content-addressed artifact store, release asset, or Git LFS only after an explicit retention decision.

Every retained external artifact should record:

- logical artifact name and type;
- SHA-256 and byte size;
- creation or retrieval time;
- source URI or run identifier;
- specification, rubric, relation-registry, and schema versions;
- code commit and configuration hash;
- model and deployment identifiers;
- prompt and response hashes;
- retry and random-seed provenance;
- storage locator and access policy.

The recovered `HM_test_v7.tar` remains outside Git. `data/manifests/source_snapshot_inventory.csv` makes its contents auditable.
