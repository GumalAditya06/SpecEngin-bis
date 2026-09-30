# Specengine BIS corpus collector

Production backend/frontend requirements and health checks are documented in
[`DEPLOYMENT.md`](DEPLOYMENT.md).

A narrow, restartable evidence collector for three BIS product domains. It only fetches approved BIS domains, preserves the `IS 2347:2017` / `IS 2347:2023` distinction, and produces raw files plus traceable metadata. It does **not** implement RAG generation, a vector database, or a retrieval API.

## Usage

Use Python 3.10 or newer:

```bash
python3 -m pip install -r requirements.txt
python3 -m scraper run --all
python3 -m scraper run --product mixer
python3 -m scraper discover --product kettle
python3 -m scraper download --all
python3 -m scraper retry-failed --all
python3 -m scraper validate
python3 -m scraper coverage
python3 -m scraper laboratories --all
python3 -m scraper metadata-check
python3 -m scraper clean
python3 -m scraper clean --product mixer
python3 -m scraper clean --all
python3 -m scraper structure
python3 -m scraper structure --product mixer
python3 -m scraper structure --all
python3 -m scraper chunk
python3 -m scraper chunk --product mixer
python3 -m scraper chunk --all
python3 -m scraper embed
python3 -m scraper embed --batch-size 32
python3 -m scraper embed --embedding-model bge-base-en-v1.5 --batch-size 16
python3 -m scraper embed --product mixer
python3 -m scraper status
python3 -m scraper extract
python3 -m scraper extract --product mixer
python3 -m scraper extract --all
```

Conservative crawl controls can be changed explicitly, for example:

```bash
python3 -m scraper run --all --delay 2 --max-depth 1 --max-pages-per-domain 20
```

Configuration (products, historical versions, keywords, official seeds, and LIMS queries) is kept in `scraper/config.py`, separate from crawler logic.

## Outputs

- `data/raw/<product>/`: validated PDFs
- `data/raw/<product>/web/`: cleaned text snapshots of relevant HTML pages
- `data/metadata/manifest.json` and `.jsonl`: document provenance and validation
- `data/metadata/laboratories.json`: scoped LIMS records
- `data/metadata/crawl_log.jsonl`: request/retry/robots events
- `data/metadata/failures.json`: inaccessible official resources
- `data/processed/extracted/`: raw page-level PyMuPDF/OCR JSON and extraction report
- `data/processed/metadata/metadata_consistency_report.json`: deterministic manifest/content consistency audit
- `data/processed/cleaned/`: conservative page-level cleaned text, embedded raw text, provenance, and cleaning report
- `data/processed/structured/`: deterministic sections, clauses, subclauses, annexes, tables, page provenance, and structure report
- `data/processed/chunks/chunks.jsonl`: clause-aware retrieval chunks with deterministic IDs and complete provenance
- `data/processed/chunks/chunking_report.json`: chunk sizes, splits, standards, tables, annexes, and metadata validation
- `data/processed/embeddings/embeddings.jsonl`: one normalized vector per chunk with complete provenance
- `data/processed/embeddings/embedding_report.json`: model identity, timings, batching, truncation, failures, and validation results
- `data/processed/embeddings/semantic_test.json`: top 5 cosine-similarity matches for each fixed sanity query
- `data/processed/chunks_v2/chunks.jsonl`: Stage 3.1A chunks, realigned to the embedding model's real tokenizer
- `data/processed/chunks_v2/chunking_report.json`: tokenizer inspection, safe-limit derivation, per-chunk audit, validation
- `data/processed/chunks_v2/comparison_report.json`: chunk-for-chunk diff against the frozen Stage 2.5 corpus
- `data/processed/embeddings_v2/`: the same three embedding artefacts, built from `chunks_v2`
- `data/processed/vector_store/index.npz`: the float32 vector matrix plus its chunk-id column
- `data/processed/vector_store/metadata.jsonl`: the metadata store, one record per row, fixed schema
- `data/processed/vector_store/index_config.json`: backend, metric, dimension, format version
- `data/processed/vector_store/index_report.json`: model, revision, counts, metric, and source hashes
- `data/processed/vector_store/retrieval_sanity_report.json`: ranked evidence for the five fixed queries

The manifest is also the resume state. Existing downloaded paths are skipped, and distinct URLs resolving to identical content are linked using SHA-256 without storing a second file.

PDF validation uses `pdfinfo` and `pdftotext` when available. Without them, signature/EOF checks still run and text extractability is reported conservatively.

`extract` without a filter performs the three-document Stage 2.2 first run. Product and `--all` filters process every validated PDF in their selection. OCR is attempted only for empty or clearly insufficient pages and remains non-fatal when Tesseract is unavailable.

`metadata-check` compares the unchanged manifest with deterministic evidence from PDF titles, filenames, URLs, and document text. `clean` audits all validated-PDF metadata and cleans only documents already present in Stage 2.2 extraction output; its default selection is the same three-manual test set.

`structure` operates only on cleaned inputs. It preserves every cleaned/raw page, detects conservative BIS-style hierarchy and explicit annex/table boundaries, and records uncertain numbered lines for review. It does not chunk or embed text.

`chunk` operates only on structured inputs. Limits and tokenizer identity are CLI-configurable; the default `regex_v1` counter stays available as a generic fallback. It never creates embeddings or vector records. `chunk-v2` is the production path and is described in the next section.

## Embedding model selection (Stage 3.1)

`embed` reads the frozen `chunks.jsonl` and writes one vector per chunk. The model is configuration, not a constant: `--embedding-model`, `$EMBEDDING_MODEL`, `--batch-size`, `$EMBEDDING_BATCH_SIZE`, `--device`, `$EMBEDDING_DEVICE`, and `--embedding-revision` all override the registered defaults in `scraper/config.py`. Every registered model carries a pinned revision so a rerun resolves to identical weights.

**Default: `BAAI/bge-base-en-v1.5`** (109.5M parameters, 768 dimensions, CLS pooling, MIT).

The four candidates were run against the real 83-chunk corpus and the five sanity queries rather than chosen on benchmark reputation alone:

| Model | Dim | Window | Over-limit chunks | Control-unit query result |
| --- | --- | --- | --- | --- |
| `bge-base-en-v1.5` | 768 | 512 | 10 | correct clause ranked 1st, 0.772 |
| `bge-small-en-v1.5` | 384 | 512 | 10 | correct clause ranked 2nd, behind IS 4250 |
| `all-MiniLM-L6-v2` | 384 | 256 | 12 | correct clause 1st, but related clauses collapse to 0.39 |
| `e5-base-v2` | 768 | 512 | 10 | competitive, but asymmetric |

Reasons for the default:

- **Ranking quality on this corpus.** It put the IS 2347 clause 1.3.1 (`"Control Unit: For the guidance of manufacturers, the recommended definition..."`) first for `"What is the definition of a control unit?"` at 0.772, with the parallel IS 4250 and IS 367 clauses at 0.754 and 0.698. `bge-small` inverted the first two; `all-MiniLM-L6-v2` scored related-but-not-identical clauses at 0.39.
- **Symmetric encoding.** Documents and queries are embedded the same way. `e5-base-v2` scored similarly but requires literal `query: ` / `passage: ` prefixes, and Stage 3.2 would silently degrade the moment a caller embeds a document without the passage prefix.
- **Standard architecture, no `trust_remote_code`.** Plain BERT weights load through stock `transformers`, so there is no repository code executing at inference time and the revision pin is meaningful.
- **Fits the hardware.** CPU-only inference runs the corpus in ~34 s with 4 GB of RAM. `bge-large` was rejected as unnecessary for 83 chunks.
- **Reproducibility.** Two full runs produced byte-identical `embeddings.jsonl` (max absolute difference 0.0) on torch 2.14.0+cpu. Batch size affects throughput but not the vectors: batch 32 and batch 7 agree to 1.4e-07, which is float32 reduction-order noise, not a correctness problem.

## Embedding behaviour

`embed` prepends the chunk's existing `context_prefix` to its `text` before encoding, so a clause body carries its `IS 4250:2025 | Annex C | Clause 1.3.1` breadcrumb. The authoritative chunk text is never rewritten, summarised, or re-cleaned; the prefix already exists in `chunks.jsonl` and both strings are stored back verbatim.

Chunks longer than the model's 512-token window are encoded from their leading tokens. 10 of the 83 chunks exceed it. The stored text stays complete and unchanged; the truncated IDs are listed in `embedding_report.json` under `truncation`.

Vector dimension is read from the loaded model, never assumed. Cosine similarity divides by the norms explicitly rather than using the dot-product shortcut, and the sanity test verifies actual unit norms instead of trusting the configuration flag.

A batch that fails is recorded in the report with its chunk IDs, reason, and error, and the run continues. Failed chunks are never silently dropped, and validation fails if any input chunk is neither embedded nor accounted for as a failure.

Outputs, all under `data/processed/embeddings/`:

- `embeddings.jsonl`: one record per chunk, full provenance plus `embedding_model`, `embedding_model_revision`, `embedding_provider`, `embedding_dimension`, `normalized`, `embedding_pooling`, and `embedding`
- `embedding_report.json`: counts, timings, batch detail, truncation, failures, and the ten validation checks
- `semantic_test.json`: top 5 chunks for each fixed sanity query, with similarity, standard, clause, page, and text

`embed` is the last stage. There is no vector database, retrieval API, reranking, or answer generation here; `chunks.jsonl` is verified byte-identical before and after every run.

## Tokenizer-aligned chunk boundaries (Stage 3.1A)

Stage 3.1 embedded all 83 Stage 2.5 chunks, but those boundaries were chosen with a `regex_v1` word counter. `regex_v1` is not the tokenizer the model sees, so it undercounted: the largest chunk was 862 regex tokens but 1006 real WordPiece tokens, and **10 of 83 chunks were silently truncated by the model**, the worst losing 494 tokens. Splitting them was the only way to stop the loss without discarding source text.

The fix is to re-derive chunk boundaries from the model's own tokenizer and rebuild the corpus. The model, the raw PDFs, the extraction, the cleaned text, the structured JSON, and the Stage 2.5 `chunks.jsonl` are all unchanged; only the boundaries between chunks move. `regex_v1` still works, and the default 450-token ceiling is one configuration value.

**Measured, not assumed.** `chunk-v2` inspects the real tokenizer before chunking anything and records the result in `chunking_report.json`: class `BertTokenizer` (fast), `model_max_length` 512, `do_lower_case` true, vocabulary 30522, and an overhead of exactly 2 special tokens (`[CLS]`, `[SEP]`), giving a usable content budget of 510. The safe ceiling is then derived rather than picked: the longest `context_prefix` in the corpus costs 56 tokens, so 512 − 2 − 56 = 454, and the default stops at **450** to leave 4 tokens of margin. Prefix and body were checked to be additive at the seam for all 83 chunks, which is what makes "budget = limit − prefix cost" sound.

**What changed.** 83 chunks became 97. 73 kept their Stage 2.5 ID and their text byte-for-byte; only 8 structural units were split, all of them tables (3), annexes (4), and front matter (1). **No clause or subclause was split**, so no numbered requirement, definition, or test step was cut in half. Maximum embedding input fell from 1006 tokens to 450, and the count of chunks the model would truncate went from 10 to 0.

Splits prefer paragraph, then sentence, then list-item/table-row boundaries, and each token edge is snapped back to a whitespace or punctuation break so a word is never cut mid-piece. Split pieces inherit `clause_number`, pages, and every document-level provenance field from the parent, and tables keep their column headings on every piece. The old and new corpora are compared chunk-for-chunk in `comparison_report.json`.

**How it is verified.** `chunk-v2` refuses to write unless all 16 validation checks pass, and the same run hashes the raw, cleaned, extracted, structured, and Stage 2.5 chunk trees before and after to prove they were not touched. No-information-loss is checked by character-offset tiling rather than string matching: every piece records the `unit_text_start`/`unit_text_end` it came from, and the union of those ranges must equal the source unit exactly, so duplicated, reordered, or rewritten text cannot hide a gap. `unit_text_sha256` ties each group of pieces back to its node in the structured input. IDs stay deterministic and content-derived — two consecutive runs produced byte-identical `chunks.jsonl` (`e9dbad20…`) — and an ID is never reused for different text. Re-embedding the 97 chunks gives 768-dimension unit vectors with `chunks_truncated: 0`, and all 11 embedding checks pass.

**Honest limits.** Splitting a table means the vector for one fragment is built from that fragment plus the column headings, not the whole table, so a question that spans rows is still weaker than a question about a single row. Row-level atomicity is not recoverable from this corpus, which flattens tables to one cell per line. The measured `regex_v1` undercount is about 7-9% on average, not uniform: it grows with unusual words and sub-word splits, which is exactly why the largest chunks were the worst hit. And with 5 fixed queries, score movement is directional evidence, not a benchmark.

Regenerate both:

```bash
.venv/bin/python -m scraper chunk-v2
.venv/bin/python -m scraper embed \
    --chunks-file data/processed/chunks_v2/chunks.jsonl \
    --output-dir data/processed/embeddings_v2
```

`chunk-v2` always uses `bge`; `regex_v1` remains available through `chunk` for the frozen Stage 2.5 behaviour, which `chunk-v2` reproduces exactly as a regression check. `$EMBEDDING_MAX_TOKENS` or `--embedding-max-tokens` overrides the ceiling.

## Vector index and retrieval (Stage 3.2)

```bash
.venv/bin/python -m scraper index
.venv/bin/python -m scraper search "What is the definition of a control unit?"
.venv/bin/python -m scraper search "sampling plan" --top-k 3
.venv/bin/python -m scraper search "control unit" --filter-product kettle
.venv/bin/python -m scraper retrieve-eval
```

### Why an exact NumPy index

The corpus is 97 vectors of 768 dimensions: 291 KiB resident, and a full
exhaustive scan costs 5 µs per query. Approximate nearest neighbour indexes
(HNSW, IVF) exist to search millions of vectors; at this size they would add a
native dependency, index build time, and recall loss in exchange for speed
nobody can measure. Exact search is also the only option that makes the
recorded scores independently checkable, since the score at rank 1 is a dot
product a reader can recompute by hand.

FAISS, Qdrant, and Chroma are all absent from the venv. That was not the deciding
factor; the scale was. The backend sits behind the `VectorStore` interface in
`scraper/vectorstore.py`, and a test substitutes a second backend that ranks in
the opposite order, so replacing it later is a real, exercised seam rather than
a claim. Swapping in FAISS or pgvector means implementing `add`, `search`,
`save`, `load`, and the four count properties; the retrieval layer is unchanged
because it never reaches into backend internals.

### Cosine similarity

Scores are cosine. Because the stored vectors are unit vectors, cosine equals
the dot product, and the scan uses a single matmul. That equivalence is measured
at build time rather than assumed: `finalize()` computes every row's L2 norm,
and the max deviation from 1.0 is recorded in `index_report.json`. For the
current corpus it is 1.08e-07, so the fast path is taken. If a future corpus
ever contains a row further than 1e-5 from unit norm, the index records
`normalized: false` and falls back to explicit division, so a score is never
reported as a cosine when it is not one.

### Query embedding

Queries are embedded by `embeddings.embed_queries`, the same function the
document pass used, so the model, pinned revision, normalization, and
`query_prefix` handling are shared code rather than a second implementation
that could drift. `bge-base-en-v1.5` is configured for symmetric encoding: no
prefix is added to a query, and none to a document, apart from the
`context_prefix` the chunk itself already carries.

### Metadata and provenance

The index holds one float32 row per chunk plus the `chunk_id` it belongs to.
The metadata store beside it carries the chunk record on a fixed schema, so a
front-matter chunk with no `clause_number` is distinguishable from one whose
clause number is unknown. Deleting the index costs a rebuild, not provenance.
Each result exposes `chunk_id`, `document_id`, `standard_numbers`,
`document_title`, `document_type`, `organization`, `section_number`,
`section_title`, `clause_number`, `clause_title`, `parent_clause_number`,
`annex_identifier`, `table_identifier`, `pages`, `start_page`, `end_page`,
`source_url`, `text`, and `context_prefix`. `text` is the stored source text
verbatim, never regenerated.

### Metadata filtering

`search(query, top_k=5, filters=None)` accepts any of `standard_number`,
`document_id`, `clause_number`, `annex_identifier`, `document_type`, and
`product`. Filters intersect, and an unknown field is rejected with the list of
available ones rather than silently matching nothing. `index_report.json`
records per-field coverage, distinguishing `supported` (every row has a value)
from `partial` (some rows carry it; the rest are excluded rather than guessed
at).

`product` is not a field of the corpus. It is joined from
`data/metadata/manifest.json` by `document_id` at index time, because the
manifest is the authority on which product each document was collected for. A
document the manifest does not cover keeps `product: null`; no product is ever
inferred from the document's text.

Filtering narrows the candidate set without reordering or rescoring what
survives, so a filtered result carries the same score it had unfiltered.

### Rebuild safety

`index_report.json` records the SHA-256 of both the embeddings file and the
chunks file it indexes. `load_index` recomputes the embeddings hash and refuses
to serve an index whose hash no longer matches, with a message naming both
digests and the rebuild command. Vectors carrying mixed models, mixed
revisions, or mixed dimensions are rejected outright rather than indexed into
one space. Building twice produces byte-identical `index.npz` and
`metadata.jsonl`; only `created_at` differs.

### What Stage 3.2 does not claim

`retrieve-eval` writes `retrieval_sanity_report.json` and reports no accuracy
percentage. This corpus has no manually verified ground truth, so any such
number would be invented; a test asserts that the report contains no
retrieval-quality metric key at all. The five fixed queries give directional
evidence about ranking behaviour, not a benchmark. Reranking, hybrid
retrieval, and answer generation are not implemented in Stage 3.2.

## Hybrid retrieval and reranking (Stage 3.3)

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -m scraper hybrid-search "control unit"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -m scraper hybrid-eval
```

Each query retrieves 20 semantic and 20 lexical candidates by default, merges
them with reciprocal rank fusion (`rrf_k=60`), reranks up to 40 unique chunks,
and returns 5. These values are configurable with `--candidate-k`,
`--rerank-k`, `--rrf-k`, and `--top-k`. Metadata filters are applied in both
candidate retrievers before the cross-encoder runs.

The lexical layer is an in-repository Okapi BM25 implementation (`k1=1.5`,
`b=0.75`). Its deterministic searchable representation contains, in this
order, `context_prefix`, authoritative `text`, `standard_numbers`,
`clause_number`, and `clause_title`; exact duplicate components are omitted.
No embeddings, URLs, file names, or unrelated metadata are indexed. Tokens are
lowercased, but punctuation inside identifiers is retained so `4250:2025` and
`1.3.1` remain single tokens.

The selected reranker is the pinned 22.7M-parameter
`cross-encoder/ms-marco-MiniLM-L6-v2` revision
`233902d25c440f23af6f7d6e94d2946bac0bee0a`. It receives the query paired with
the chunk's structural `context_prefix` and unmodified `text`. Its raw logits
are ranking scores, not probabilities. The evaluation report compares it on
the real fused BIS pools against the 19.2M-parameter L4 alternative. L6 costs a
few more CPU milliseconds but has stronger published passage-ranking results
and promoted direct testing/compliance clauses on the broad frozen testing
query. BGE reranker-base was inspected but its roughly 1.1 GB weights were not
a CPU-MVP-sized trade-off for at most 40 pairs.

`hybrid-eval` writes `bm25_report.json`, `hybrid_report.json`,
`reranker_report.json`, and `comparison_report.json` under
`data/processed/retrieval_v2/`. The comparison leaves every 0/1/2 relevance
label as `null`; it does not invent judgements or an accuracy percentage.
Ranks are stable for fixed inputs and runtime versions, with explicit
chunk-ID tie breaks. Wall-clock timing and tiny floating-point differences can
vary across CPU, PyTorch, and BLAS versions.

Stage 3.3 remains retrieval-only: there is no LLM, answer generation, prompt,
chat endpoint, citation generator, or conversational memory.

## Retrieval service and evidence contract (Stage 3.4)

Stage 3.4 wraps the unchanged `HybridRetriever` with one long-lived
`RetrievalService`. The service loads BGE, BM25, the existing vector store, and
the pinned cross-encoder once, then exposes:

```python
service.retrieve(query, top_k=5, filters=None)
```

The fixed Stage 3.3 policy remains 20 semantic candidates, 20 BM25 candidates,
RRF with `k=60`, at most 40 reranked unique candidates, and at most 5 returned
results. The public filters are exactly those supported by the current metadata
layer: `standard_number`, `document_id`, `clause_number`, `annex_identifier`,
`document_type`, and `product`. `source_id` is not filterable because it is not
present in the indexed metadata. Short exact identifiers (`IS 367`, `IS 2347`,
and `IS 4250`) resolve to the corpus edition and are applied as a pre-reranking
standard filter. If a future corpus contains multiple editions for one short
identifier, the service requires the year rather than guessing.

Install the service extra and run the local API from the project root:

```bash
uv pip install --python .venv/bin/python -e '.[embeddings,service]'
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/uvicorn scraper.retrieval_api:app --host 127.0.0.1 --port 8001
```

`POST /api/v1/search/retrieve` accepts `query`, `top_k` (1–5), and `filters`.
Each result contains the authoritative chunk text, structural context, standard
and clause identity, nullable source provenance, and the semantic/BM25/RRF/
reranker trace. Missing source IDs or document versions are `null`; they are
never derived from filenames or standard years. Timings remain internal to the
service contract and are recorded for performance evaluation rather than sent
as UI debugging data.

This is still a retrieval-only API. It does not generate answers, citations,
prompts, chat messages, or conversational state.

## Evidence context builder (Stage 4.1)

`EvidenceContextBuilder` is the provider-neutral boundary between the frozen
`RetrievalService` and a future grounded answerer:

```python
from scraper.evidence_context import EvidenceContextBuilder

retrieved = service.retrieve(query)
package = EvidenceContextBuilder().build(query, retrieved.results)
```

The resulting `EvidencePackage` contains the query, included `EvidenceItem`
objects, count/availability fields, deterministic `context_text`, the explicit
character budget, and metadata for any whole evidence blocks omitted by that
budget. Each item receives `E1`, `E2`, and so on directly from its retrieval
rank. Inputs must remain in contiguous rank order, and duplicate chunk IDs,
missing authoritative text, invalid ranks, or inconsistent packages are
rejected rather than repaired or re-ranked.

The text format labels structured provenance, then separates `Context:` from
`Authoritative Text:`. Source text and context prefixes are copied exactly;
they are never summarized, normalized, or paraphrased. Nullable structured
fields remain JSON `null`. Only the display representation renders a missing
value as `Unavailable`, so Python `None` never leaks into future model context.

The default `max_context_chars` is 20,000. Budgeting is deterministic and
rank-preserving: it retains the highest-ranked prefix of complete evidence
blocks. At the first block that cannot fit, that item and all lower-ranked
items are listed in `omitted_evidence` with reason
`context_budget_exceeded`. Authoritative evidence is never truncated.

Stage 4.1 performs no retrieval, ranking, answer generation, prompting, model
inference, chat, streaming, or memory. A future Stage 4.2 adapter may consume
the package, but no LLM provider is referenced by this module.

## Grounded answer generation (Stage 4.2)

Stage 4.2 adds a provider-neutral `LLMProvider` protocol and a validated answer
orchestrator after the frozen retrieval and evidence-context stages:

```text
RetrievalService -> EvidenceContextBuilder -> GroundedAnswerService
                 -> LLMProvider -> AnswerValidator -> GroundedAnswer
```

The real adapter follows the repository's existing Google Gemini REST
convention and targets `gemini-3.6-flash` unless `BIS_LLM_MODEL` explicitly
selects another model. Credentials and provider selection are environment-only;
there is no implicit provider fallback:

```bash
cp .env.example .env
export BIS_LLM_PROVIDER=gemini
export BIS_LLM_MODEL=gemini-3.6-flash
export BIS_LLM_API_KEY='...'
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/uvicorn scraper.assistant_api:app --host 127.0.0.1 --port 8001
```

The adapter sends only the grounding system instruction, user query, and Stage
4.1 `context_text`. Retrieved text is delimited as untrusted data, and no tools,
web search, database access, filesystem access, chat history, or autonomous
actions are available. Gemini structured output is configured with temperature
`0.0`, at most 1,200 output tokens, JSON MIME type, and a fixed answer schema.

`GroundedAnswer` contains `query`, `answer`, one of
`VERIFIED_EVIDENCE`/`PARTIAL_EVIDENCE`/`NO_VERIFIED_EVIDENCE`, validated
citations, limitations, included structured evidence, and operational metadata.
The model returns citation IDs only; the application reconstructs all citation
metadata from the supplied `EvidencePackage`. The validator rejects malformed
JSON, blank answers, unknown or duplicate IDs, citations absent from answer
text, invalid states, and partial answers without limitations.

Generation allows at most two attempts. A transient provider failure or invalid
structured draft gets one retry; authentication/request errors are not retried.
Zero evidence bypasses the provider and returns `NO_VERIFIED_EVIDENCE`
deterministically. Observability records provider/model, retrieval/context/
generation latency, evidence count, state, attempts, and normalized token usage;
it does not record keys or full prompts.

`POST /api/v1/assistant/query` is exposed by `scraper.assistant_api:app` and
accepts the same `query`, `top_k`, and supported filters as retrieval. The
existing `/api/v1/search/retrieve` endpoint remains available and unchanged.
There is no streaming or conversational memory in Stage 4.2.
## Stage 4.4 — RAG evaluation and benchmarking

The evaluation-only layer in `scraper/rag_evaluation.py` measures the frozen
retrieval stages independently from grounded answer generation. Run
`.venv/bin/python -m scraper evaluate --retrieval-only` without an LLM key, or
use `--answers` with the existing provider configuration. The versioned dataset,
methodology, manual groundedness workflow, and generated artefacts are documented
in `data/evaluation/README.md`. No production ranking or answer configuration is
changed by the evaluator.

## Stage 4.5 — Knowledge entity integration

`scraper.knowledge_entities.KnowledgeEntityService` is a read-only projection
over the existing manifest, product configuration, structured documents,
Stage 3.1A chunks, metadata consistency report, and LIMS snapshot. It exposes
typed `SourceDocument`, `Standard`, `StructuralUnit`, `ProductEntity`,
`Laboratory`, `TestingRequirement`, `Service`, provenance, and relationship
contracts from `scraper.entity_models` without introducing a graph database or
writing a second copy of the corpus.

The public application interface supports `get_source_document`,
`get_standard`, `get_documents_for_standard`, `get_standards_for_product`,
`get_structural_units`, `get_related_chunks`,
`get_laboratories_for_standard`, `get_testing_requirements`,
`get_relationships`, and `get_related_sources`. Unknown entities safely return
`None` or an empty collection. Generated entity and relationship identifiers
are deterministic SHA-256-derived identifiers; source document IDs and product
IDs retain their existing authoritative identities.

Relationship authority is explicit: product/document and standard/document
links come from the manifest, product/standard links from `PRODUCTS`, structure
from the structured corpus, chunk links from `chunks_v2`, and laboratory links
from the LIMS snapshot. Testing requirements are emitted only for structured
units with explicit testing, inspection, or laboratory markers. Metadata audit
conflicts propagate as `REQUIRES_REVIEW`; missing metadata stays null or
`UNVERIFIED`. The current frontend Services page is editorial workflow copy,
not an authoritative service registry, so Stage 4.5 defines the `Service`
contract but deliberately creates no service entities or relationships.

## Stage 4.6 — Evidence sufficiency and out-of-domain guard

`scraper.evidence_sufficiency.EvidenceSufficiencyGuard` runs after the unchanged
`EvidenceContextBuilder` and before provider generation. It classifies an
immutable evidence package as `SUFFICIENT`, `PARTIAL`, `INSUFFICIENT`, or
`OUT_OF_DOMAIN`. These map respectively to `VERIFIED_EVIDENCE`,
`PARTIAL_EVIDENCE`, `NO_VERIFIED_EVIDENCE`, and `NO_VERIFIED_EVIDENCE` in the
existing answer contract.

The guard combines existing reranker scores, BM25/semantic participation,
lexical overlap, configured product-to-standard associations, exact standard,
clause, annex and table identifiers, whole-query segment coverage, and context
budget omissions. Exact identifiers that resolve to supplied evidence are
accepted even when semantic scores are low. It uses no topic blacklist, LLM,
embedding model, reranker, web source, or new retrieval step.

The provisional reranker support floor is `0.0`: in the frozen Stage 4.4
dataset, the minimum top score for 22 verified positive questions is `1.069578`
and the only candidate-returning negative question scores `-10.849028`. This is
not claimed as a formal classifier threshold because the calibration set has
only one such negative; a result also needs lexical, BM25, configured-product,
exact-identifier, or strong semantic agreement. The decision and its signals
are returned as an additive `assessment`, while observability records guard
latency, candidate/support counts, and whether a Gemini call was avoided.

Run the separate evaluation without changing Stage 4.4 reports:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -m scraper sufficiency-eval
```

It writes `data/evaluation/stage_4_6_evaluation_report.json`.
