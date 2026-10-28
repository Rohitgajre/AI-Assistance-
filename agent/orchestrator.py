"""Controlled orchestration layer."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.router import QuestionRouter
from agent.tool_schemas import ConversationContextInput, DocumentSearchInput, TavilySearchInput
from agent.tools.conversation_context import ConversationContextTool
from agent.tools.document_search import DocumentSearchTool
from agent.tools.structured_analysis import StructuredAnalysisTool
from agent.tools.tavily_search import TavilySearchTool

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    answer: str
    sources: list
    tools_used: list[str]
    reasoning: str


class Orchestrator:
    def __init__(self, max_iterations: int = 3, timeout_seconds: int = 60):
        self.router = QuestionRouter()
        self.doc_search = DocumentSearchTool()
        self.tavily = TavilySearchTool()
        self.structured = StructuredAnalysisTool()
        self.conv_context = ConversationContextTool()
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
    
    def orchestrate(self, question: str, document_ids: list[str] | None = None,
                    has_documents: bool = False, has_bank_statement: bool = False,
                    conversation_id: str | None = None) -> OrchestrationResult:
        decision = self.router.route(question, has_documents, has_bank_statement)
        tools_used = []
        sources = []
        context_parts = []
        
        # Get conversation context if needed
        if decision.needs_conversation_context and conversation_id:
            try:
                ctx = self.conv_context.execute(ConversationContextInput(
                    query=question,
                    conversation_id=conversation_id
                ))
                if ctx.context:
                    context_parts.append(f"Conversation history:\n{ctx.context}")
                tools_used.append("conversation_context")
            except Exception as e:
                logger.warning(f"Failed to get conversation context: {e}")
        
        # Document search
        if decision.needs_document_rag and has_documents:
            try:
                results = self.doc_search.execute(DocumentSearchInput(
                    query=question,
                    document_ids=document_ids or [],
                    top_k=5
                ))
                if results.results:
                    for r in results.results:
                        context_parts.append(f"[Doc] {r.get('text', '')}")
                        sources.append({
                            "type": "document",
                            "filename": r.get('metadata', {}).get('filename'),
                            "page": r.get('metadata', {}).get('page_number')
                        })
                tools_used.append("document_search")
            except Exception as e:
                logger.warning(f"Document search failed: {e}")
        
        # Web search
        if decision.needs_web_search:
            try:
                results = self.tavily.execute(TavilySearchInput(query=question, max_results=5))
                if results.results:
                    for r in results.results:
                        context_parts.append(f"[Web] {r.get('title')}: {r.get('content')}")
                        sources.append({
                            "type": "web",
                            "title": r.get('title'),
                            "url": r.get('url')
                        })
                tools_used.append("tavily_search")
            except Exception as e:
                logger.warning(f"Web search failed: {e}")
        
        # Build answer with gathered context
        if not context_parts:
            return OrchestrationResult(
                answer="I don't have enough context to answer this question. Please upload relevant documents or enable web search.",
                sources=sources,
                tools_used=tools_used,
                reasoning=decision.reason
            )
        
        combined_context = "\n\n".join(context_parts)
        
        # Use LLM to generate answer
        try:
            from chat_service import ask_model
            from providers.llm_provider import get_llm_provider
            
            prompt = f"""Answer the question using the provided context. Cite sources clearly.
Distinguish between document sources and web sources.

Context:
{combined_context[:15000]}

Question: {question}

Answer:"""
            
            model = get_llm_provider().get_chat_model()
            answer = ask_model(model, prompt)
            
            return OrchestrationResult(
                answer=answer,
                sources=sources,
                tools_used=tools_used,
                reasoning=decision.reason
            )
        except Exception as e:
            logger.error(f"Orchestration answer generation failed: {e}")
            return OrchestrationResult(
                answer=f"Error: {str(e)}",
                sources=sources,
                tools_used=tools_used,
                reasoning=decision.reason
            )


_orchestrator = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
