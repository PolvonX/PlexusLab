from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import chromadb
from chromadb.config import Settings


class VectorStore:
    """Простое векторное хранилище на базе ChromaDB для семантической памяти."""

    def __init__(self, data_dir: Path | str) -> None:
        self.memory_dir = Path(data_dir) / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        
        # PersistentClient сохраняет данные локально в указанную папку
        self.client = chromadb.PersistentClient(
            path=str(self.memory_dir),
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(name="cortex_memory")

    def memorize(self, doc_id: str, topic: str, content: str, tags: List[str]) -> None:
        """Сохраняет фрагмент знаний в базу данных с векторами."""
        metadata = {
            "topic": topic,
            "tags": ",".join(tags) if tags else ""
        }
        
        self.collection.upsert(
            documents=[content],
            metadatas=[metadata],
            ids=[doc_id]
        )

    def recall(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """Ищет топ-N релевантных фрагментов по текстовому запросу."""
        count = self.collection.count()
        if count == 0:
            return []
            
        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, count)
        )
        
        if not results.get("documents") or not results["documents"][0]:
            return []
            
        docs = results["documents"][0]
        metas = results["metadatas"][0] if results.get("metadatas") else []
        
        out = []
        for i, doc in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            out.append({"content": doc, "metadata": meta})
            
        return out
