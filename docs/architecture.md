# Architecture — MaternalCare AI (Phase 1, local RAG)

Rendered images: `docs/img/pipeline.png`, `docs/img/components.png`, `docs/img/answer_flow.png`.
The diagrams below are Mermaid; GitHub renders them directly.

## 1. Pipeline: offline indexing and online query (all on one laptop)

```mermaid
flowchart LR
  subgraph Offline["Offline indexing (run once / when PDFs change)"]
    A["30 guideline PDFs<br/>+ data/sources.csv"] --> B["ingestion.py<br/>load, clean, drop TOC/refs,<br/>token chunking 400/60"]
    B --> C[("processed_chunks.json<br/>5,078 chunks")]
    C --> D["embeddings.py<br/>BAAI/bge-m3 (1024-d)"]
    D --> E[("ChromaDB, cosine<br/>vector_db/")]
  end
  subgraph Online["Online query (Streamlit / CLI)"]
    Q["Question AR / EN"] --> EC["emergency_check.py<br/>danger signs, no LLM"]
    EC -->|"danger sign"| BN["Emergency banner<br/>(added by code)"]
    EC --> QT["query_transform.py<br/>Arabic: + English search query"]
    QT --> R["retriever.py<br/>top-k = 4, threshold 0.57,<br/>best score of both queries"]
    E --> R
    R -->|"no chunk above threshold"| NF["Not-found reply<br/>(LLM not called)"]
    R --> P["prompts.py<br/>grounded EN/AR prompt"]
    P --> L["llm.py: Ollama<br/>qwen2.5:7b, temp 0, num_ctx 8192"]
    L --> F["dosage_filter.py<br/>doses, medicine advice,<br/>diagnosis"]
    F --> O["Answer + [n] citations<br/>+ similarity score + disclaimer"]
    BN --> O
  end
```

## 2. Components and files

```mermaid
flowchart TB
  subgraph UI["Interfaces"]
    APP["app.py (Streamlit, st.tabs)"]
    CLI["cli.py (ingest / index / ask / eval)"]
  end
  subgraph RAG["rag/"]
    ING["ingestion.py"]
    EMB["embeddings.py"]
    IDX["index_builder.py"]
    RET["retriever.py"]
    PRM["prompts.py"]
    LLM["llm.py"]
    PIPE["rag_pipeline.py"]
    QTR["query_transform.py"]
    SCH["schemas.py"]
  end
  subgraph SAFE["safety/"]
    ART["arabic_text.py"]
    DOS["dosage_filter.py"]
    DIS["disclaimer.py"]
    EMC["emergency_check.py"]
  end
  subgraph STORE["Storage"]
    VDB[("vector_db/ (Chroma)")]
    SQL[("db/patient_records.db<br/>schema v1, Phase 2")]
  end
  CFG["config.py + .env"]
  OLL["Ollama server<br/>127.0.0.1:11434"]
  APP --> PIPE
  CLI --> PIPE
  CLI --> ING
  CLI --> IDX
  PIPE --> RET --> EMB
  RET --> VDB
  IDX --> EMB
  IDX --> VDB
  ING --> ART
  PIPE --> PRM
  PIPE --> LLM --> OLL
  PIPE --> DOS --> ART
  PIPE --> DIS
  PIPE --> EMC --> ART
  PIPE --> QTR --> LLM
  CLI --> SQL
  CFG -.-> RAG
```

## 3. answer_question(): where each safety rule is enforced in code

```mermaid
sequenceDiagram
  participant U as User
  participant P as rag_pipeline
  participant R as retriever
  participant M as LLM (Ollama)
  participant F as dosage_filter
  U->>P: question (AR or EN)
  P->>P: detect_language()
  P->>P: check_emergency() (rules, no LLM)
  opt Arabic question
    P->>M: write a short English search query
  end
  P->>R: search_multi([question, English query], k=4, threshold 0.57)
  R-->>P: chunks with similarity = 1 - cosine distance
  alt no chunk above threshold
    P-->>U: [emergency banner] + fixed not-found sentence + disclaimer (answer LLM NOT called)
  else chunks found
    P->>M: grounded prompt (numbered context, rules)
    M-->>P: full answer (not streamed)
    P->>P: read [n] markers -> citations (invalid ones dropped)
    P->>F: filter_dosage + filter_medication_advice + filter_diagnosis
    F-->>P: answer with unsafe sentences replaced
    P-->>U: [emergency banner] + answer + citations + similarity scores + disclaimer (added by code)
  end
```

## 4. Design decisions (short)

| Decision | Why |
|---|---|
| Fully local (Ollama, bge-m3, Chroma) | Health questions never leave the laptop (privacy, Egyptian Law 151/2020); no API keys or costs |
| bge-m3 embeddings | Multilingual: an Arabic question finds English guideline text; 8,192-token window |
| Token-based chunks of 400 / overlap 60, never across pages | Measured in the embedder's own tokens; one page per chunk = exact page citations |
| Cosine distance, similarity = 1 − distance | Vectors are normalized; cosine compares direction (meaning), not length |
| Calibrated threshold (0.57) | Chosen from in-scope vs out-of-scope top-1 similarities (eval/retrieval_eval.py), with the query transformation on |
| Emergency check before retrieval, no LLM | Danger signs must get the 'go to emergency now' message even if nothing is retrieved; deterministic rules are testable (preliminary, full interceptor in Phase 2) |
| Arabic → English search query | Egyptian-dialect questions were far from the English guideline text; searching with both raised Arabic document hit@4 from 0.56 to 0.78 |
| Generation limits + one retry (num_predict, repeat_penalty) | Ollama aborted one answer with "token repeat limit reached"; the rewrite and the answer now have token caps, a retry, and a clear message instead of an error |
| `127.0.0.1` instead of `localhost` | On Windows `localhost` tries IPv6 first and each new connection waited ~2 s (measured 2,483 ms vs 324 ms) |
| Output filters in code (dose, medicine advice, diagnosis) | The 7B model broke these prompt rules in the manual test; code filters make the rules hold |
| No LLM call without context | The model cannot invent an answer when nothing relevant was found |
| Dosage filter after generation, no streaming | The prompt alone cannot guarantee "no doses"; the filter must see the full answer first |
| Chroma called directly | Full control over the distance function, IDs (idempotent upsert) and metadata |
| `st.tabs` UI, stable function interfaces | Phase 2 adds tabs and wraps `retrieve()` as an agent tool without rewriting |
