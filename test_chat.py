import asyncio
from app.services.llm_client import get_llm_client
import logging
from app.database.connection import init_pool

logging.basicConfig(level=logging.DEBUG)

async def test_llm():
    client = get_llm_client()
    
    q_rag = "What is the penalty for illegal business?"
    
    structured_history = [
        {"role": "user", "content": "What is a contract?"},
        {"role": "assistant", "content": "It is an agreement."},
        {"role": "user", "content": "Thanks!"},
        {"role": "assistant", "content": "You are welcome!"},
    ]
    
    print("\nTesting structured history...")
    result = client.ask(
        question=q_rag,
        structured_history=structured_history,
        context_markdown="Legal context about illegal business penalties...",
        session_summary="User was asking about contracts.",
        is_conversational=False
    )
    
    print(f"Answer: {result['answer']}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    init_pool()
    asyncio.run(test_llm())
