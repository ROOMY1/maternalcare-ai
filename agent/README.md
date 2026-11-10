# Phase 2 — AI Agent (not implemented yet)

This folder is a placeholder. Phase 2 (the AI Agent assignment, 20 marks) starts only after
Phase 1 is finished and handed over. There is **no agent code** in Phase 1, and `langgraph` is not
a dependency yet.

## Planned design (from the team master plan, updated for a fully local stack)

`start → safety_node (deterministic emergency interceptor) → [emergency: save reading + reply, end]
| perception_node (Arabic normalization, Egyptian drug-name resolution, query expansion)
→ agent (local LLM via Ollama + 6 tools) → dosage_filter → end`

- Memory: LangGraph `SqliteSaver` in `db/checkpoints.db`, one thread per session
  (`f"{patient_id}-{session_id}"`); long-term facts in `db/patient_records.db`.
- `patient_id` is injected from the runtime config; the LLM never supplies it.
- Tools: `search_maternal_guidelines`, `check_food_safety`, `log_fetal_kicks`,
  `log_and_analyze_vitals`, `book_patient_appointment`, `parse_lab_results`.
- The agent uses the **same local Ollama model** with tool calling (no Groq/Gemini), so patient
  messages never leave the laptop.

## Phase 1 contracts Phase 2 relies on (do not change without telling the team)

| Contract | Used in Phase 2 as |
|---|---|
| `rag.retriever.retrieve()` + `format_for_tool()` | the `search_maternal_guidelines` tool body |
| `rag.llm.get_chat_model()` (supports `.bind_tools()`, tested in `check_env.py`) | the agent LLM node |
| `safety.dosage_filter.filter_dosage()` (pure, no I/O) | the last graph node |
| `safety.arabic_text.normalize()` / `to_ascii_digits()` / `detect_language()` | interceptor + perception |
| `storage.database.init_db()` + schema v1 (`patients.edd`, no `chat_sessions`) | tool save/read functions + dashboard |
| `rag.rag_pipeline.answer_question()` → `RAGResponse` | fallback path + Phase 1 vs Phase 2 comparison |
| `app.py` built on `st.tabs` | new tabs are added, not a rewrite |

## Phase 2 ownership

| Member | Takes over from Phase 1 |
|---|---|
| 1. Data, drugs, food safety | `rag/ingestion.py`, `data/sources.csv` |
| 2. Retrieval and memory | `rag/embeddings.py`, `rag/index_builder.py`, `rag/retriever.py`, `rag/rag_pipeline.py`, benchmark |
| 3. Agent core and DB | `rag/prompts.py`, `rag/llm.py`, `storage/database.py` |
| 4. Safety, perception, vitals, environment (Abdelrahman Islam) | `safety/dosage_filter.py`, `safety/arabic_text.py`, `check_env.py`, `requirements.txt`, README |
| 5. UI and evaluation | `app.py`, `eval/*` |
