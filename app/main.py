from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.routers import (
    chat, auth, students, documents, complaints, 
    admin, rag, embedding, leads, universities, majors, program_intakes
)
from app.config import settings

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="MalishaEdu AI Enrollment Agent",
    description="AI-powered enrollment assistant for Chinese universities",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(students.router, prefix="/api/students", tags=["students"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(complaints.router, prefix="/api/complaints", tags=["complaints"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(rag.router, prefix="/api/rag", tags=["rag"])
app.include_router(embedding.router, prefix="/api/embed", tags=["embedding"])
app.include_router(leads.router, prefix="/api/leads", tags=["leads"])
app.include_router(universities.router, prefix="/api/universities", tags=["universities"])
app.include_router(majors.router, prefix="/api/majors", tags=["majors"])
app.include_router(program_intakes.router, prefix="/api/program-intakes", tags=["program-intakes"])

@app.get("/")
async def root():
    return {"message": "MalishaEdu AI Enrollment Agent API", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

