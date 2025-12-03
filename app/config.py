from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List, Optional

class Settings(BaseSettings):
    DATABASE_URL: str
    OPENAI_API_KEY: str
    TAVILY_API_KEY: str
    OPENAI_MODEL: str = "gpt-4.1"
    OPENAI_ROUTER_MODEL: str = "gpt-4.1-mini"
    OPENAI_DISTILL_MODEL: str = "gpt-4.1"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    
    # R2 settings - support both naming conventions
    R2_ACCESS_KEY: str = Field(default="", alias="R2_ACCESS_KEY_ID")
    R2_SECRET_KEY: str = Field(default="", alias="R2_SECRET_ACCESS_KEY")
    R2_BUCKET_URL: str = Field(default="", alias="R2_PUBLIC_URL")
    R2_ENDPOINT_URL: str = Field(default="", alias="R2_API_DEFAULT_VALUE")
    
    # Allow extra fields from .env
    R2_ACCOUNT_ID: Optional[str] = None
    R2_BUCKET_NAME: Optional[str] = None
    R2_API_TOKEN_VALUE: Optional[str] = None
    
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    GROQ_API_KEY: str = ""
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore extra fields in .env

settings = Settings()

