import os
import re
import shutil
import requests
from pypdf import PdfReader

def sanitize_filename(filename):
    return re.sub(r'[<>:"/\\|?*]', '_', filename)

def fix_caps(text):
    """If text is all uppercase, convert to title case."""
    if text and text.isupper():
        return text.title()
    return text

def extract_doi_from_text(text):
    match = re.search(r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+', text, re.IGNORECASE)
    return match.group(0) if match else None

def extract_isbn_from_text(text):
    match = re.search(r'ISBN[-‐–]?(1[03])?:?\s*(97(8|9))?\d{9}[\dX]', text.replace('\n', ''), re.IGNORECASE)
    if match:
        return re.sub(r'[^0-9X]', '', match.group(0))[-13:]
    return None

def extract_ids_from_pdf(reader, max_pages=10):
    for i in range(min(max_pages, len(reader.pages))):
        text = reader.pages[i].extract_text()
        if text:
            doi = extract_doi_from_text(text)
            if doi:
                return 'doi', doi
            isbn = extract_isbn_from_text(text)
            if isbn:
                return 'isbn', isbn
    return None, None

def query_crossref(doi):
    url = f"https://api.crossref.org/works/{doi}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json().get("message", {})
    except Exception as e:
        print(f"[ERROR] CrossRef lookup failed: {e}")
    return {}

def query_openlibrary(isbn):
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        return data.get(f"ISBN:{isbn}", {})
    except Exception as e:
        print(f"[ERROR] Open Library lookup failed: {e}")
    return {}

def main():
    input_folder = os.getcwd()
    base_output = os.path.join(input_folder, "Renamed-PDFs")
    complete_folder = os.path.join(base_output, "Complete")
    incomplete_folder = os.path.join(base_output, "Incomplete")
    os.makedirs(complete_folder, exist_ok=True)
    os.makedirs(incomplete_folder, exist_ok=True)

    for filename in os.listdir(input_folder):
        if filename.lower().endswith('.pdf'):
            file_path = os.path.join(input_folder, filename)
            try:
                reader = PdfReader(file_path)
                metadata = reader.metadata or {}

                # Extract raw metadata
                raw_author = metadata.get('/Author')
                raw_title = metadata.get('/Title')
                raw_date = metadata.get('/CreationDate')

                # Enrich missing fields
                if not (raw_author and raw_title and raw_date):
                    id_type, id_value = extract_ids_from_pdf(reader)
                    if id_type == 'doi':
                        data = query_crossref(id_value)
                        if not raw_author:
                            authors = data.get("author", [])
                            if authors:
                                raw_author = authors[0].get("family", "") or "UnknownAuthor"
                        if not raw_title:
                            raw_title = data.get("title", ["UnknownTitle"])[0]
                        if not raw_date:
                            year = data.get("published-print", {}).get("date-parts", [[None]])[0][0]
                            if not year:
                                year = data.get("published-online", {}).get("date-parts", [[None]])[0][0]
                            if year:
                                raw_date = f"D:{year}0000000000"

                    elif id_type == 'isbn':
                        data = query_openlibrary(id_value)
                        if not raw_author:
                            authors = data.get("authors", [])
                            if authors:
                                raw_author = authors[0].get("name", "UnknownAuthor")
                        if not raw_title:
                            raw_title = data.get("title", "UnknownTitle")
                        if not raw_date:
                            year = data.get("publish_date", "")
                            match = re.search(r"\b\d{4}\b", year)
                            if match:
                                raw_date = f"D:{match.group(0)}0000000000"

                # Determine completeness BEFORE cleaning
                is_complete = bool(raw_author and raw_title and raw_date)

                # Clean + fix caps for filename construction
                author_fixed = fix_caps(raw_author or "UnknownAuthor")
                title_fixed = fix_caps(raw_title or "UnknownTitle")

                author_clean = sanitize_filename(author_fixed.replace(" ", "_").split('_')[-1])
                title_clean = sanitize_filename(title_fixed.replace(" ", "_"))

                # Truncate long titles to 40 characters
                max_title_length = 40
                if len(title_clean) > max_title_length:
                    title_clean = title_clean[:max_title_length].rstrip('_')

                # Format year
                if raw_date and raw_date.startswith('D:'):
                    year_clean = sanitize_filename(raw_date[2:6])
                elif raw_date:
                    year_clean = sanitize_filename(raw_date[:4])
                else:
                    year_clean = "UnknownYear"

                # Create new filename and path
                new_filename = f"{author_clean} - {year_clean} - {title_clean}.pdf"
                output_folder = complete_folder if is_complete else incomplete_folder
                new_path = os.path.join(output_folder, new_filename)

                if not os.path.exists(new_path):
                    shutil.copy(file_path, new_path)
                    print(f"[{('✓' if is_complete else '⚠')}] '{filename}' → '{new_filename}'")
                else:
                    print(f"[SKIP] '{new_filename}' already exists")

            except Exception as e:
                print(f"[ERROR] Couldn't process '{filename}': {e}")

if __name__ == '__main__':
    main()
