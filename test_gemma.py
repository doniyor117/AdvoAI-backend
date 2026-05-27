import asyncio
from app.services.llm_client import get_llm_client
import logging

logging.basicConfig(level=logging.INFO)

async def test_llm():
    client = get_llm_client()
    
    q_conv = "Hello, how are you today?"
    q_rag = "What is the penalty for illegal business?"
    
    print("Testing conversational query:")
    intent1 = client.route_query(q_conv)
    print(f"Query: {q_conv} => Intent: {intent1}")
    
    print("\nTesting RAG query:")
    intent2 = client.route_query(q_rag)
    print(f"Query: {q_rag} => Intent: {intent2}")
    
    print("\nTesting summarize archive:")
    old_messages = "User: What is a contract?\nAssistant: A contract is a legal agreement.\nUser: Thanks!\nAssistant: You're welcome!"
    summary = client.summarize_archive(old_messages, previous_summary="")
    print(f"Summary generated: {summary}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    from app.database.connection import init_pool
    init_pool()
    asyncio.run(test_llm())
