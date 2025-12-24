from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, RateLimitError
from app.config import settings
from typing import List, Dict, Optional
import json
import time

class OpenAIService:
    def __init__(self):
        # Configure OpenAI client with timeout and retry settings
        self.client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=300.0,  # 5 minutes timeout for long SQL generation
            max_retries=3  # Retry up to 3 times
        )
        self.model = settings.OPENAI_MODEL
        self.router_model = settings.OPENAI_ROUTER_MODEL
        self.distill_model = settings.OPENAI_DISTILL_MODEL
        self.embedding_model = settings.OPENAI_EMBEDDING_MODEL
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding using text-embedding-3-small"""
        try:
            response = self.client.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            # Handle regional restrictions or API errors gracefully
            print(f"Error generating embedding (may be regional restriction): {e}")
            raise
    
    def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts"""
        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=texts
        )
        return [item.embedding for item in response.data]
    
    def chat_completion(
        self, 
        messages: List[Dict[str, str]], 
        temperature: float = 0.7,
        top_p: float = 1.0,
        stream: bool = False,
        max_retries: int = 3
    ):
        """Generate chat completion with retry logic"""
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    top_p=top_p,
                    stream=stream,
                    timeout=300.0  # 5 minutes timeout
                )
                return response
            except (APIConnectionError, APITimeoutError) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 2  # Exponential backoff: 2s, 4s, 8s
                    print(f"⚠️  OpenAI connection error (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ OpenAI connection failed after {max_retries} attempts")
                    raise
            except RateLimitError as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = 60  # Wait 60 seconds for rate limit
                    print(f"⚠️  OpenAI rate limit (attempt {attempt + 1}/{max_retries}). Waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ OpenAI rate limit exceeded after {max_retries} attempts")
                    raise
            except Exception as e:
                # For other errors, don't retry
                print(f"❌ OpenAI API error: {str(e)}")
                raise
        
        # Should not reach here, but just in case
        if last_exception:
            raise last_exception
    
    def distill_content(self, content: str, context: str) -> str:
        """Distill and extract key information from content"""
        messages = [
            {
                "role": "system",
                "content": f"""You are a knowledge extraction assistant. Extract and distill key information from the following content, focusing on:
- University names and programs
- Tuition fees
- Accommodation fees
- Admission requirements
- Intake dates (March/September)
- Scholarship information
- Visa requirements
- Location information

Return a structured summary in JSON format."""
            },
            {
                "role": "user",
                "content": f"Context: {context}\n\nContent to distill:\n{content}"
            }
        ]
        
        response = self.client.chat.completions.create(
            model=self.distill_model,
            messages=messages,
            temperature=0.3
        )
        return response.choices[0].message.content
    
    def reflect_and_improve(self, answer: str, rag_context: str, tavily_context: Optional[str] = None, is_scholarship_chance: bool = False) -> str:
        """Reflection method to improve answer accuracy"""
        # NEVER use reflection for scholarship chance questions - they have strict format requirements
        if is_scholarship_chance:
            return answer
        
        # Build tavily context string separately to avoid backslash in f-string
        tavily_section = ""
        if tavily_context:
            tavily_section = f"Tavily Search Context:\n{tavily_context}\n\n"
        
        messages = [
            {
                "role": "system",
                "content": """You are a quality assurance assistant. Review the answer and improve it by:
1. Checking if RAG facts were used correctly
2. Verifying accuracy of information
3. Ensuring clarity and completeness
4. Combining RAG and web search results when available
5. Making the answer more helpful and encouraging

CRITICAL: Do NOT add external links, URLs, or "Useful Links" sections.
Do NOT add scholarship details, types, or coverage examples unless the question specifically asks for them.
Do NOT add "How to Improve", "Summary", or similar sections unless the question asks for them.

Return the improved answer."""
            },
            {
                "role": "user",
                "content": f"""Original Answer:
{answer}

RAG Context:
{rag_context}

{tavily_section}Please review and improve this answer."""
            }
        ]
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.5
        )
        return response.choices[0].message.content

