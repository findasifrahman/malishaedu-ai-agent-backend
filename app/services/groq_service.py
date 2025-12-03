from groq import Groq
from app.config import settings
from typing import List, Dict, Optional

class GroqService:
    def __init__(self):
        if settings.GROQ_API_KEY:
            self.client = Groq(api_key=settings.GROQ_API_KEY)
            self.available = True
        else:
            self.available = False
    
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str = "llama-3-70b-8192",
        temperature: float = 0.7,
        top_p: float = 1.0
    ):
        """Generate chat completion using Groq"""
        if not self.available:
            raise Exception("Groq API key not configured")
        
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=top_p
        )
        return response

