"""Question router to determine which tools/capabilities to use."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RouteDecision:
    needs_document_rag: bool = False
    needs_structured_analysis: bool = False
    needs_web_search: bool = False
    needs_conversation_context: bool = False
    reason: str = ""


class QuestionRouter:
    def route(self, question: str, has_documents: bool = False, has_bank_statement: bool = False) -> RouteDecision:
        q_lower = question.lower()
        decision = RouteDecision()
        
        # Check for follow-up indicators
        follow_up_patterns = ['explain that', 'more detail', 'compare with', 'earlier', 'previous']
        if any(p in q_lower for p in follow_up_patterns):
            decision.needs_conversation_context = True
            decision.reason += "Follow-up question detected. "
        
        # Check for calculation/aggregation needs
        calc_patterns = ['total', 'sum', 'calculate', 'average', 'count', 'how much', 'spending by']
        if any(p in q_lower for p in calc_patterns):
            if has_bank_statement or 'rows where' in q_lower or 'group by' in q_lower:
                decision.needs_structured_analysis = True
                decision.reason += "Calculation/aggregation needed. "
            elif has_documents:
                decision.needs_document_rag = True
                decision.reason += "Data query on documents. "
        
        # Check for web/current info needs
        web_patterns = ['current', 'latest', 'today', 'recent news', 'price of', 'weather', 'stock price']
        if any(p in q_lower for p in web_patterns):
            decision.needs_web_search = True
            decision.reason += "Current/external information needed. "
        
        # Default to document RAG if documents available
        if has_documents and not decision.needs_web_search and not decision.needs_structured_analysis:
            # Check if it's a general question about documents
            if not any(p in q_lower for p in ['search web', 'google']):
                decision.needs_document_rag = True
                if not decision.reason:
                    decision.reason = "Document question - using RAG"
        
        return decision
