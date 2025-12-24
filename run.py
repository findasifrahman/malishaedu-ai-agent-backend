import uvicorn
import os

if __name__ == "__main__":
    # Disable reload in production (Railway sets RAILWAY_ENVIRONMENT)
    reload = os.getenv("RAILWAY_ENVIRONMENT") is None and os.getenv("ENVIRONMENT") != "production"
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=reload,
        timeout_keep_alive=300,  # Keep connections alive for 5 minutes (Railway paid plan)
        timeout_graceful_shutdown=30
    )

