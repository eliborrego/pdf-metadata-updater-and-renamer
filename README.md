# 📄 PDF Auto-Renamer with DOI and ISBN Lookup
Automatically renames PDF files using embedded metadata or online lookups.  
Renames files to the format:  
**`AuthorLastName - Year - Title.pdf`**

## 🔍 What it does
- Extracts embedded PDF metadata (`Author`, `Title`, `Year`)
- If missing, searches the first 10 pages for:
  - ✅ **DOI** → queried via [CrossRef](https://www.crossref.org/)
  - ✅ **ISBN** → queried via [Open Library](https://openlibrary.org/dev/docs/api/books)
- Sorts files into:
  - 📂 `Renamed-PDFs/Complete/` (all fields found)
  - 📂 `Renamed-PDFs/Incomplete/` (some fields missing)

## ⚙️ Use Cases
- Clean up your downloaded research papers
- Organize digital book collections
- Prepare files for reference managers (like Zotero, EndNote, Mendeley)
