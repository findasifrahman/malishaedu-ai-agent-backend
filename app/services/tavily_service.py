from tavily import TavilyClient
from app.config import settings
from typing import List, Dict

class TavilyService:
    def __init__(self):
        self.client = TavilyClient(api_key=settings.TAVILY_API_KEY)
    
    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        """Search the web using Tavily"""
        try:
            response = self.client.search(
                query=query,
                max_results=max_results,
                search_depth="advanced"
            )
            
            results = []
            for result in response.get("results", []):
                results.append({
                    "title": result.get("title", ""),
                    "url": result.get("url", ""),
                    "content": result.get("content", ""),
                    "score": result.get("score", 0)
                })
            
            return results
        except Exception as e:
            print(f"Tavily search error: {e}")
            return []
    
    def format_search_results(self, results: List[Dict]) -> str:
        """Format search results into a readable string"""
        if not results:
            return ""
        
        formatted = "Web Search Results:\n\n"
        for i, result in enumerate(results, 1):
            formatted += f"{i}. {result['title']}\n"
            formatted += f"   URL: {result['url']}\n"
            formatted += f"   Content: {result['content'][:500]}...\n\n"
        
        return formatted

