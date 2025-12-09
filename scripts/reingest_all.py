"""
Re-Ingest Script: Clears database and re-ingests all papers with improved concept linking.

Usage:
    python scripts/reingest_all.py

This script will:
1. Clear the entire Neo4j database
2. Re-ingest all PDFs from data/uploads/
3. Use improved concept extraction with better text matching
4. Create proper Paper -> Section -> Paragraph -> Concept hierarchy
"""
import sys
from pathlib import Path
import time

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.neo import Neo4jClient
from scripts.ingest import ingest_one


def clear_database(neo: Neo4jClient) -> None:
    """Clear entire Neo4j database in batches to avoid memory issues."""
    print("\n" + "="*60)
    print("CLEARING DATABASE")
    print("="*60)
    
    # Delete in batches to avoid memory issues on AuraDB free tier
    batch_size = 1000
    
    # Step 1: Delete relationships in batches
    print("  Deleting relationships in batches...")
    while True:
        result = neo.run(
            f"MATCH ()-[r]->() WITH r LIMIT {batch_size} DELETE r RETURN count(r) AS deleted"
        )
        deleted = result[0]["deleted"] if result else 0
        if deleted == 0:
            break
        print(f"    Deleted {deleted} relationships...")
    
    print("  ✓ All relationships deleted")
    
    # Step 2: Delete nodes in batches
    print("  Deleting nodes in batches...")
    while True:
        result = neo.run(
            f"MATCH (n) WITH n LIMIT {batch_size} DELETE n RETURN count(n) AS deleted"
        )
        deleted = result[0]["deleted"] if result else 0
        if deleted == 0:
            break
        print(f"    Deleted {deleted} nodes...")
    
    print("  ✓ All nodes deleted")
    
    # Step 3: Verify deletion
    result = neo.run("MATCH (n) RETURN count(n) AS remaining")
    count = result[0]["remaining"] if result else 0
    if count == 0:
        print("  ✓ Database cleared successfully")
    else:
        print(f"  ⚠ Warning: {count} nodes remaining")
        raise Exception("Database not fully cleared")


def find_pdf_files(upload_dir: Path) -> list[Path]:
    """Find all PDF files in upload directory."""
    if not upload_dir.exists():
        raise FileNotFoundError(f"Upload directory not found: {upload_dir}")
    
    pdf_files = list(upload_dir.glob("*.pdf"))
    print(f"\nFound {len(pdf_files)} PDF files in {upload_dir}")
    for pdf in pdf_files:
        print(f"  - {pdf.name}")
    
    return pdf_files


def main():
    print("\n" + "="*60)
    print("RE-INGEST ALL PAPERS")
    print("="*60)
    print("\nThis will:")
    print("  1. DELETE all data in Neo4j")
    print("  2. Re-ingest all PDFs with improved concept linking")
    print("  3. This may take 30-60 minutes depending on number of papers")
    
    response = input("\nContinue? (yes/no): ").strip().lower()
    if response != "yes":
        print("Aborted.")
        return
    
    # Initialize Neo4j client
    neo = Neo4jClient()
    
    # Step 1: Clear database
    clear_database(neo)
    
    # Step 2: Find all PDFs
    upload_dir = Path(__file__).parent.parent / "data" / "uploads"
    pdf_files = find_pdf_files(upload_dir)
    
    if not pdf_files:
        print("\n⚠ No PDF files found. Please add PDFs to data/uploads/")
        return
    
    # Step 3: Ingest all papers
    print("\n" + "="*60)
    print("INGESTING PAPERS")
    print("="*60)
    
    success_count = 0
    failed_papers = []
    
    start_time = time.time()
    
    for i, pdf_path in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] Processing: {pdf_path.name}")
        print("-" * 60)
        
        try:
            # Ingest paper (uses improved concept_extract.py)
            ingest_one(Path(pdf_path), neo)
            success_count += 1
            print(f"✓ Successfully ingested: {pdf_path.name}")
            
        except Exception as e:
            print(f"✗ Failed to ingest {pdf_path.name}: {e}")
            failed_papers.append((pdf_path.name, str(e)))
    
    elapsed = time.time() - start_time
    
    # Step 4: Summary
    print("\n" + "="*60)
    print("INGESTION SUMMARY")
    print("="*60)
    print(f"Total papers: {len(pdf_files)}")
    print(f"Successfully ingested: {success_count}")
    print(f"Failed: {len(failed_papers)}")
    print(f"Time elapsed: {elapsed/60:.1f} minutes")
    
    if failed_papers:
        print("\nFailed papers:")
        for name, error in failed_papers:
            print(f"  - {name}: {error}")
    
    # Step 5: Run diagnostics
    print("\n" + "="*60)
    print("RUNNING DIAGNOSTICS")
    print("="*60)
    
    diagnostic_queries = [
        ("Total Papers", "MATCH (p:Paper) RETURN count(p) AS count"),
        ("Total Paragraphs", "MATCH (p:Paragraph) RETURN count(p) AS count"),
        ("Total Concepts", "MATCH (c:Concept) RETURN count(c) AS count"),
        ("Total Sections", "MATCH (s:Section) RETURN count(s) AS count"),
        ("MENTIONS Relations", "MATCH ()-[r:MENTIONS]->() RETURN count(r) AS count"),
        ("Paragraphs WITH Concepts", """
            MATCH (para:Paragraph)
            WITH count(para) AS total
            MATCH (para2:Paragraph)-[:MENTIONS]->()
            WITH total, count(DISTINCT para2) AS withConcepts
            RETURN withConcepts AS count, 
                   round(withConcepts * 100.0 / total) AS percentage
        """),
        ("Empty Sections", """
            MATCH (sec:Section)
            WHERE NOT ((sec)-[:HAS_PARAGRAPH]->())
            RETURN count(sec) AS count
        """),
        ("Orphan Paragraphs", """
            MATCH (p:Paper)-[:HAS_PARAGRAPH]->(para:Paragraph)
            WHERE NOT EXISTS ((:Section)-[:HAS_PARAGRAPH]->(para))
            RETURN count(para) AS count
        """)
    ]
    
    for desc, query in diagnostic_queries:
        try:
            result = neo.run(query)
            if result:
                row = result[0]
                if "percentage" in row:
                    print(f"  {desc}: {row['count']} ({row['percentage']:.1f}%)")
                else:
                    print(f"  {desc}: {row['count']}")
        except Exception as e:
            print(f"  {desc}: Error - {e}")
    
    print("\n" + "="*60)
    print("RE-INGEST COMPLETE")
    print("="*60)
    print("\nNext steps:")
    print("  1. Check diagnostics above")
    print("  2. If 'Paragraphs WITH Concepts' is low, check concept extraction logs")
    print("  3. Run CLEANUP_EMPTY_SECTIONS.md scripts if needed")
    print("  4. Visualize graph in Neo4j Browser")


if __name__ == "__main__":
    main()
