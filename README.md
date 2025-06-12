# PDF Metadata Renamer
A Python script that automatically renames PDF files based on their metadata (author, year, title) using reliable academic databases and APIs. 
The script prioritizes using DOI, ISBN, and arXiv identifiers to fetch metadata from authoritative sources.

## Features
- Metadata Extraction: Searches PDFs for DOI, ISBN, and arXiv identifiers
- Multiple API Sources: Queries CrossRef, Semantic Scholar, arXiv, and Open Library APIs
- Reliable Naming: Creates consistent filenames in the format: Author - Year - Title.pdf
- Safe Operation: Always creates backups before renaming files
- Quality Control: Moves problematic files to a needs-attention folder for manual review
- International Support: Handles non-Latin characters through transliteration
- Batch Processing: Process entire directories of PDFs with progress tracking


## How It Works
### Metadata Priority
The script uses a hierarchical approach to ensure reliable metadata:

1. Identifier Search: Scans PDF content for DOI, ISBN, or arXiv IDs
2. API Lookups: Queries academic databases in order of:
- DOI → CrossRef and Semantic Scholar
- arXiv ID → arXiv API and Semantic Scholar
- ISBN → Open Library
3. Title Search: If no identifiers found, attempts Semantic Scholar title search
4. PDF Metadata: Falls back to embedded PDF metadata only as last resort

### File Organization
5. Successfully Renamed: Files with complete, verified metadata are renamed in place
6. Needs Attention: Files are moved to needs-attention/ folder if they have:
- No identifiers found (DOI/ISBN/arXiv)
- Incomplete metadata (missing author, year, or title)
- Processing errors

### Backups
7. Original files are backed up to .pdf_backup/ before any changes

### Filename Format
8. Renamed files follow the pattern:
- LastName - Year - Title.pdf
