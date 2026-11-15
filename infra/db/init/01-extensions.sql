-- Runs once, the first time the database container starts with an empty volume.
-- (To re-run it: `docker compose down -v` deletes all data, then start again.)

-- pgvector: vector columns and similarity search, for embeddings (doc 06 §2.5).
CREATE EXTENSION IF NOT EXISTS vector;

-- A separate database for automated tests, so tests never touch development data.
CREATE DATABASE eureka_test;
\connect eureka_test
CREATE EXTENSION IF NOT EXISTS vector;
