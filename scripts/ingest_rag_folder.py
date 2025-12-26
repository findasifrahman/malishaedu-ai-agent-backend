"""
CLI script to ingest markdown documents from a folder into RAG database.
Usage: python -m scripts.ingest_rag_folder <folder_path> [--doc-type <type>] [--audience <audience>] [--version <version>]
"""
import sys
import os
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.services.rag_service import RAGService

def determine_doc_type_from_filename(filename: str) -> str:
    """Determine doc_type from filename patterns"""
    filename_lower = filename.lower()
    if 'csca' in filename_lower:
        return 'csca'
    elif 'partner' in filename_lower or 'b2b' in filename_lower:
        return 'b2b_partner'
    elif 'contact' in filename_lower or 'people' in filename_lower:
        return 'people_contact'
    elif 'service' in filename_lower or 'policy' in filename_lower or 'charge' in filename_lower:
        return 'service_policy'
    else:
        return 'b2c_study'

def ingest_folder(
    folder_path: str,
    doc_type: str = None,
    audience: str = 'student',
    version: str = None
):
    """Ingest all markdown files from a folder"""
    folder = Path(folder_path)
    if not folder.exists():
        print(f"Error: Folder {folder_path} does not exist")
        return
    
    db: Session = SessionLocal()
    rag_service = RAGService()
    
    md_files = list(folder.glob("*.md"))
    if not md_files:
        print(f"No .md files found in {folder_path}")
        return
    
    print(f"Found {len(md_files)} markdown files")
    
    for md_file in md_files:
        try:
            print(f"\nProcessing: {md_file.name}")
            
            # Read file content
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Determine doc_type if not provided
            file_doc_type = doc_type or determine_doc_type_from_filename(md_file.name)
            
            # Determine audience from filename if not provided
            file_audience = audience
            if 'partner' in md_file.name.lower() or 'b2b' in md_file.name.lower():
                file_audience = 'partner'
            
            # Ingest the file
            result = rag_service.ingest_source(
                db=db,
                name=md_file.name,
                doc_type=file_doc_type,
                audience=file_audience,
                version=version,
                source_url=None,
                last_verified_at=None,
                full_text=content
            )
            
            print(f"  ✓ Ingested: {result['chunks_created']} chunks created, {result['chunks_skipped']} skipped")
            
        except Exception as e:
            print(f"  ✗ Error processing {md_file.name}: {e}")
            continue
    
    db.close()
    print("\n✓ Ingestion complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest markdown files from a folder into RAG database")
    parser.add_argument("folder", help="Path to folder containing markdown files")
    parser.add_argument("--doc-type", choices=['csca', 'b2c_study', 'b2b_partner', 'people_contact', 'service_policy'],
                       help="Document type (auto-detected from filename if not provided)")
    parser.add_argument("--audience", choices=['student', 'partner'], default='student',
                       help="Target audience (default: student)")
    parser.add_argument("--version", help="Document version (e.g., '2026', 'v1.0')")
    
    args = parser.parse_args()
    
    ingest_folder(
        folder_path=args.folder,
        doc_type=args.doc_type,
        audience=args.audience,
        version=args.version
    )


