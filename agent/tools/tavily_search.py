"""Tavily web search tool."""
from __future__ import annotations

import logging
import os

from agent.tool_schemas import TavilySearchInput, TavilySearchOutput

logger = logging.getLogger(__name__)


class TavilySearchTool:
    name = "tavily_search"
    description = "Search the web for current information using Tavily"
    
    def __init__(self):
        self.api_key = os.getenv("TAVILY_API_KEY")
    
    def execute(self, input: TavilySearchInput) -> TavilySearchOutput:
        if not self.api_key:
            logger.warning("TAVILY_API_KEY not configured")
            return TavilySearchOutput(results=[], query=input.query)
        
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=self.api_key)
            response = client.search(
                query=input.query,
                max_results=input.max_results,
                search_depth="basic"
            )
            results = response.get("results", [])
            # Normalize results
            normalized = []
            for r in results:
                normalized.append({
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "content": r.get("content"),
                    "score": r.get("score"),
                    "source_type": "web"
                })
            return TavilySearchOutput(results=normalized, query=input.query)
        except Exception as e:
            logger.error(f"Tavily search failed: {e}")
            return TavilySearchOutput(results=[], query=input.query)
    
    def get_schema(self):
        return TavilySearchInput, TavilySearchOutput
