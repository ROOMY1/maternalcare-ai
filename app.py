"""Streamlit web UI for MaternalCare AI (Phase 1: "Guideline Assistant" tab).

Purpose
    A chat page where a user asks in Arabic or English and gets a grounded answer with its
    sources (file, title, page, similarity score), safety badges and latency.

Layout
    - st.tabs with one tab now, so Phase 2 adds "Patient Assistant", "Doctor Dashboard" and
      "Appointments" without restructuring.
    - Disclaimer banner (EN + AR) at the top of every session.
    - Sidebar: models, index stats, the document list, and a re-index button with confirmation.

Safety
    The full answer is generated, filtered by the dosage filter and only then displayed
    (no token streaming). Chat history is display-only and is not sent to the LLM in Phase 1.

Run:  streamlit run app.py

Author: Abdelrahman Islam.
"""

from __future__ import annotations

import csv
import html
from typing import Any

import streamlit as st

from config import SOURCES_CSV, get_settings
from rag.schemas import RAGResponse
from safety.disclaimer import DISCLAIMER, EMERGENCY_BANNER

st.set_page_config(page_title="MaternalCare AI", page_icon="🤰", layout="wide")
SETTINGS = get_settings()

# Arabic text inside an element with dir="auto" is shown right-to-left automatically.
st.markdown(
    """<style>
    .mc-answer {font-size: 1.02rem; line-height: 1.7; unicode-bidi: plaintext;}
    .mc-snippet {font-size: 0.88rem; color: #555; border-left: 3px solid #ddd; padding-left: .6rem;
                 unicode-bidi: plaintext; white-space: pre-wrap;}
    </style>""",
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------- cached resources
@st.cache_resource(show_spinner="Loading the embedding model and the index...")
def load_retriever() -> Any:
    """Embedder + Chroma collection, loaded once per server process."""
    from rag.retriever import get_retriever

    return get_retriever()


@st.cache_resource(show_spinner=False)
def load_llm() -> Any:
    from rag.llm import get_chat_model

    return get_chat_model()


@st.cache_data(show_spinner=False)
def load_sources() -> list[dict[str, str]]:
    with open(SOURCES_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------------- helpers
def rich(text: str, css: str = "mc-answer") -> None:
    """Show text safely (HTML-escaped) in a dir="auto" block so Arabic renders right-to-left."""
    body = html.escape(text).replace("\n", "<br>")
    st.markdown(f'<div dir="auto" class="{css}">{body}</div>', unsafe_allow_html=True)


def friendly_error(exc: Exception) -> str:
    """Turn known failures into one message that says how to fix it."""
    from rag.llm import OllamaUnavailableError
    from rag.retriever import IndexMismatchError, IndexNotReadyError

    if isinstance(exc, (OllamaUnavailableError, IndexNotReadyError, IndexMismatchError)):
        return str(exc)
    msg = str(exc)
    if "Connect" in type(exc).__name__ or "connection" in msg.lower():
        return ("Ollama is not reachable. Start the Ollama app (or run `ollama serve`), "
                f"then check `ollama list` shows `{SETTINGS.llm_model}`.")
    if "repeat limit" in msg.lower() or "prediction aborted" in msg.lower():
        return "The model started repeating itself and stopped. Please ask again, maybe in other words. / النموذج بدأ يكرر نفسه فتوقف، من فضلك اسألي مرة أخرى بصياغة مختلفة."
    if "not found" in msg.lower() and "model" in msg.lower():
        return f"The model is not pulled. Run: `ollama pull {SETTINGS.llm_model}`"
    return f"Unexpected error: {exc}"


def render_response(resp: RAGResponse) -> None:
    """Answer text, badges, sources expander and latency."""
    answer, _, _ = resp.answer.partition("\n\n---\n")  # disclaimer is shown separately below
    banner = EMERGENCY_BANNER[resp.language]
    if resp.emergency:
        st.error(banner, icon="🚨")  # red banner above the answer, whatever the answer is
        answer = answer.replace(banner, "", 1).strip()
    rich(answer)
    badges = st.container(horizontal=True)
    with badges:
        if resp.not_found:
            st.badge("Not found in the guidelines", icon=":material/search_off:", color="orange")
        if resp.dosage_filtered:
            st.badge("Dosing information removed", icon=":material/medication:", color="red")
        if resp.advice_filtered:
            st.badge("Medicine advice / diagnosis removed (ask your doctor)",
                     icon=":material/medication:", color="red")
        if resp.citations and not resp.cited:
            st.badge("Sources retrieved, not cited by the model", color="gray")
        if resp.emergency:
            st.badge("Emergency signs: " + ", ".join(resp.emergency_categories),
                     icon=":material/emergency:", color="red")
    st.caption(DISCLAIMER[resp.language])

    if resp.citations:
        label = "Sources (cited)" if resp.cited else "Sources (retrieved)"
        with st.expander(f"{label} · {len(resp.citations)}"):
            for c in resp.citations:
                mark = f"[{c.marker}] " if c.marker else ""
                st.markdown(f"**{mark}{c.title}**  \n`{c.source}` · page **{c.page}** · "
                            f"similarity score **{c.similarity_score:.2f}**")
                chunk = next((r for r in resp.retrieved
                              if r.source == c.source and r.page == c.page), None)
                if chunk:
                    snippet = chunk.text[:600] + ("..." if len(chunk.text) > 600 else "")
                    rich(snippet, "mc-snippet")
    lat = resp.latency_ms
    st.caption(f"⏱ query rewrite {lat.transform:.0f} ms · embed {lat.embed:.0f} ms · search {lat.search:.0f} ms · "
               f"LLM {lat.llm:.0f} ms · total {lat.total:.0f} ms")


# ----------------------------------------------------------------------------- sidebar
def sidebar() -> None:
    st.sidebar.title("🤰 MaternalCare AI")
    st.sidebar.caption("Local RAG · Phase 1")
    st.sidebar.markdown(f"**LLM:** `{SETTINGS.llm_model}` (Ollama)  \n"
                        f"**Embeddings:** `{SETTINGS.embed_model}`  \n"
                        f"**Top-k:** {SETTINGS.top_k} · **threshold:** {SETTINGS.similarity_threshold}")
    try:
        retr = load_retriever()
        meta = retr.collection.metadata or {}
        st.sidebar.markdown(f"**Index:** {retr.collection.count()} chunks · built "
                            f"{meta.get('built_at', '?')}  \nchunk {meta.get('chunk_size_tokens')} "
                            f"/ overlap {meta.get('chunk_overlap_tokens')} tokens")
    except Exception as exc:  # noqa: BLE001
        st.sidebar.error(friendly_error(exc))

    sources = load_sources()
    with st.sidebar.expander(f"Indexed documents ({len(sources)})"):
        for cat in sorted({s["category"] for s in sources}):
            st.markdown(f"**{cat}**")
            for s in sources:
                if s["category"] == cat:
                    st.markdown(f"- [{s['title']}]({s['url']}) — {s['organization']} {s['year']}"
                                f" ({s['language']})")

    with st.sidebar.expander("Re-index"):
        st.write("Re-reads the PDFs and updates the vector index. Takes about 2 minutes on GPU.")
        sure = st.checkbox("I understand, re-index now")
        if st.button("Re-index", disabled=not sure, type="primary"):
            from rag.index_builder import build_from_file
            from rag.ingestion import ingest

            with st.spinner("Ingesting and indexing..."):
                try:
                    summary = ingest()
                    stats = build_from_file()
                    load_retriever.clear()
                    st.success(f"{summary.chunks} chunks indexed ({stats.collection_count} in Chroma).")
                except Exception as exc:  # noqa: BLE001
                    st.error(friendly_error(exc))


# ----------------------------------------------------------------------------- main tab
def guideline_assistant(pending: str | None) -> None:
    """Render the Guideline Assistant tab. `pending` is the question being answered now; it comes
    from the chat input pinned at the bottom of the page (st.chat_input inside a tab is drawn
    inline, above the messages)."""
    st.info(DISCLAIMER["en"], icon="ℹ️")
    rich(DISCLAIMER["ar"], "mc-snippet")
    st.write("")

    msgs = st.session_state.messages
    for i, m in enumerate(msgs):
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                rich(m["content"])
                # A user message with no reply after it (and not being answered now) means a run was
                # interrupted: say so instead of leaving an empty gap.
                next_is_user = i + 1 < len(msgs) and msgs[i + 1]["role"] == "user"
                last_unanswered = i == len(msgs) - 1 and not pending
                if next_is_user or last_unanswered:
                    st.warning("This question was not answered (the run was interrupted). Please ask "
                               "it again. / لم تصل الإجابة على هذا السؤال، من فضلك اسأليه مرة أخرى.")
            elif "error" in m:
                st.error(m["error"])
            else:
                render_response(RAGResponse(**m["response"]))

    if not pending:
        return
    with st.chat_message("assistant"):
        with st.spinner("Searching the guidelines and writing the answer…"):
            try:
                from rag.llm import check_ollama
                from rag.rag_pipeline import answer_question

                check_ollama()
                resp = answer_question(pending, retriever=load_retriever(), llm=load_llm())
                msgs.append({"role": "assistant", "response": resp.model_dump()})
            except Exception as exc:  # noqa: BLE001 - every failure becomes a visible message
                msgs.append({"role": "assistant", "error": friendly_error(exc)})
    st.session_state.pending = None
    st.rerun()  # re-enables the chat input and shows the stored answer


def main() -> None:
    sidebar()
    st.title("MaternalCare AI")
    st.caption("Answers only from curated WHO / NICE / FDA / MotherToBaby guidelines, with page citations.")
    st.session_state.setdefault("messages", [])  # display only; not sent to the LLM in Phase 1
    st.session_state.setdefault("pending", None)  # question being answered right now
    # Called at page level (not inside the tab) so Streamlit pins it to the bottom of the window.
    # Disabled while an answer is being generated: a new question would otherwise restart the
    # script and the answer in progress would be lost (the "empty reply" bug).
    question = st.chat_input("Ask about pregnancy care… / اسألي عن رعاية الحمل…",
                             disabled=st.session_state.pending is not None)
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        st.session_state.pending = question
        st.rerun()  # redraw at once with the input disabled, then answer
    (tab_guidelines,) = st.tabs(["Guideline Assistant"])  # Phase 2 adds more tabs here
    with tab_guidelines:
        guideline_assistant(st.session_state.pending)


main()
