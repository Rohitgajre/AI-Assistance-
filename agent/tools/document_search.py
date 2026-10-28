"""Document search tool."""
from __future__ import annotations

from agent.tool_schemas import DocumentSearchInput, DocumentSearchOutput
from rag.retrieval import get_retriever


class DocumentSearchTool:
    name = "document_search"
    description = "Search through uploaded documents for relevant information"
    
    def __init__(self):
        self.retriever = get_retriever()
    
    def execute(self, input: DocumentSearchInput) -> DocumentSearchOutput:
        results = self.retriever.retrieve(
            query=input.query,
            top_k=input.top_k,
            document_ids=input.document_ids if input.document_ids else None
        )
        return DocumentSearchOutput(results=results, count=len(results))
    
    def get_schema(self):
        return DocumentSearchInput, DocumentSearchOutput
