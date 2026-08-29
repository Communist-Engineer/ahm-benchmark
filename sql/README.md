# Database and analytical SQL

- `migrations/0001_spec_v0_5_0.sql` contains the PostgreSQL 16+ reference schema and validation guards extracted from specification v0.5.0.
- `duckdb/v0_5_0_analysis.sql` contains the canonical DuckDB analysis views from specification v0.5.0.

The PostgreSQL migration is a reference implementation pending deployment validation. Its pgvector statements remain optional when embeddings stay outside the active design. Production migration tooling should split extensions, schema, seed registries, and validation into transactional revisions while preserving the specified semantics.
