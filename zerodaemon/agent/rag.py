"""RAG: FAISS vector store over scan results and threat intel.

On first use the fastembed model (BAAI/bge-small-en-v1.5, ~130 MB) is
downloaded automatically and cached by fastembed.  Subsequent starts load
from disk in < 1 s.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level singletons — set by init_store() at startup.
_store = None
_embeddings = None
_index_path: str = "zerodaemon_rag"


def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
        _embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    return _embeddings


def init_store(index_path: str = "zerodaemon_rag") -> None:
    """Load an existing FAISS index from disk, or create a fresh one."""
    global _store, _index_path
    _index_path = index_path
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document

    emb = _get_embeddings()
    path = Path(index_path)
    if path.exists():
        try:
            _store = FAISS.load_local(str(path), emb, allow_dangerous_deserialization=True)
            logger.info("RAG: loaded FAISS index from %s", path)
            return
        except Exception as exc:
            logger.warning("RAG: failed to load existing index (%s) — starting fresh", exc)

    _store = FAISS.from_documents(
        [Document(page_content="ZeroDaemon knowledge base initialised", metadata={"type": "init"})],
        emb,
    )
    _store.save_local(str(path))
    logger.info("RAG: created new FAISS index at %s", path)


def add_scan(scan_id: str, target: str, summary: str, raw_json: str) -> None:
    """Embed a completed scan result into the vector store."""
    if _store is None:
        return
    from langchain_core.documents import Document
    doc = Document(
        page_content=f"Nmap scan of {target}: {summary}\nRaw details: {raw_json[:800]}",
        metadata={"type": "scan", "scan_id": scan_id, "target": target},
    )
    _store.add_documents([doc])
    _store.save_local(_index_path)
    logger.debug("RAG: indexed scan %s for %s", scan_id, target)


def add_threat_intel(query: str, results_text: str) -> None:
    """Embed a threat-intel result into the vector store."""
    if _store is None:
        return
    from langchain_core.documents import Document
    doc = Document(
        page_content=f"Threat intel for '{query}':\n{results_text[:1200]}",
        metadata={"type": "threat_intel", "query": query},
    )
    _store.add_documents([doc])
    _store.save_local(_index_path)
    logger.debug("RAG: indexed threat intel for query '%s'", query)


def search(query: str, k: int = 5) -> list[dict]:
    """Semantic search over the knowledge base. Returns up to k results."""
    if _store is None:
        return []
    docs = _store.similarity_search(query, k=k)
    return [{"content": d.page_content, "metadata": d.metadata} for d in docs]
