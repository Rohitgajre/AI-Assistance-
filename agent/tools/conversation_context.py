"""Conversation context tool."""
from __future__ import annotations

from agent.tool_schemas import ConversationContextInput, ConversationContextOutput
from memory.conversation_store import get_conversation_store


class ConversationContextTool:
    name = "conversation_context"
    description = "Retrieve relevant conversation history"
    
    def __init__(self):
        self.store = get_conversation_store()
    
    def execute(self, input: ConversationContextInput) -> ConversationContextOutput:
        if not input.conversation_id:
            return ConversationContextOutput(messages=[], context="")
        
        messages = self.store.get_conversation_messages(
            input.conversation_id, 
            limit=input.max_messages
        )
        msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
        context = "\n\n".join(f"{m.role}: {m.content}" for m in msg_dicts)
        return ConversationContextOutput(messages=msg_dicts, context=context)
    
    def get_schema(self):
        return ConversationContextInput, ConversationContextOutput
