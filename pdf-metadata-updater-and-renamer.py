#!/usr/bin/env python3
"""
PDF Renaming Script - Reliable Metadata Version
==================================================

This script automatically renames PDF files based on their metadata (author, year, title).
It prioritizes reliable sources (DOI/ISBN lookups) over potentially incorrect filename parsing.

Key Features in v2:
- No filename parsing - only trusts PDF content and API data
- Always creates backups before renaming
- Moves uncertain files to needs-attention folder
- Moves failed files to needs-attention folder
- Smart title truncation at 70 characters
- Character transliteration for non-Latin characters
- Better handling of technical PDFs

Metadata Priority:
1. DOI/ISBN/arXiv API lookups (most reliable)
2. Semantic Scholar title search (if no identifiers)
3. PDF internal metadata (fallback only)

Author: Eli Borrego
"""

import os
import re
import shutil
import requests
import time
import logging
import unicodedata
import argparse
import json
from typing import Tuple, Optional, Dict, Any, List
from pypdf import PdfReader
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher

# Try to import tqdm for progress bars (optional dependency)
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    print("Note: Install 'tqdm' for progress bars: pip install tqdm")

# Configuration constants
CONFIG = {
    'MAX_TITLE_LENGTH': 70,        # Maximum characters for title in filename
    'MAX_PAGES_TO_SEARCH': 15,     # How many pages to search for DOI/ISBN
    'API_TIMEOUT': 15,             # Timeout for API requests in seconds
    'API_DELAY': 0.5,              # Delay between API calls to respect rate limits
    'CREATE_BACKUP': True,         # Always create backup before renaming
    'BACKUP_FOLDER': '.pdf_backup', # Hidden folder for backups
    'DRY_RUN': False,              # Test mode without actually moving files
    'ENABLE_LOGGING': False,       # Enable/disable logging
    'LOG_FILE': 'pdf_renamer.log', # Log file name
    'PROBLEM_FOLDER': 'needs-attention', # Folder for files that need verification
    'COLON_REPLACEMENT': ' -',     # What to replace colons with in filenames
    'USE_SEMANTIC_SCHOLAR': True,  # Enable Semantic Scholar API
    'USE_ARXIV': True,             # Enable arXiv API
    'FUZZY_MATCH_THRESHOLD': 0.85, # Threshold for fuzzy title matching
}

class PDFRenamer:
    """Main class for PDF renaming functionality"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize the PDF renamer with given configuration"""
        self.config = CONFIG.copy()
        if config:
            self.config.update(config)
        
        self.logger = self._setup_logging() if self.config['ENABLE_LOGGING'] else None
        self.stats = {
            'processed': 0,
            'successful': 0,
            'needs_attention': 0,
            'failed': 0,
            'skipped': 0
        }
        # Cache for API results to avoid duplicate queries
        self.api_cache = {}
    
    def _setup_logging(self) -> logging.Logger:
        """Set up logging configuration for the script."""
        logger = logging.getLogger('PDFRenamer')
        logger.setLevel(logging.INFO)
        
        # Clear any existing handlers
        logger.handlers = []
        
        # File handler
        fh = logging.FileHandler(self.config['LOG_FILE'], encoding='utf-8')
        fh.setLevel(logging.INFO)
        
        # Console handler (only for warnings and errors)
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        
        # Formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        
        logger.addHandler(fh)
        logger.addHandler(ch)
        
        return logger
    
    def log(self, level: str, message: str):
        """Log a message if logging is enabled"""
        if self.logger:
            getattr(self.logger, level.lower())(message)
    
    def transliterate_to_latin(self, text: str) -> str:
        """Convert non-Latin characters to their closest Latin equivalents."""
        if not text:
            return text
        
        # First, try Unicode normalization to decompose accented characters
        normalized = unicodedata.normalize('NFD', text)
        
        # Remove combining characters (accents, diacritics)
        no_accents = ''.join(char for char in normalized 
                            if unicodedata.category(char) != 'Mn')
        
        # Handle specific character mappings that NFD doesn't catch
        char_map = {
            # Czech/Slovak
            'ř': 'r', 'Ř': 'R', 'ď': 'd', 'Ď': 'D', 'ť': 't', 'Ť': 'T',
            'ň': 'n', 'Ň': 'N', 'ľ': 'l', 'Ľ': 'L',
            
            # Polish
            'ł': 'l', 'Ł': 'L', 'ż': 'z', 'Ż': 'Z',
            
            # German
            'ß': 'ss',
            
            # Scandinavian
            'ø': 'o', 'Ø': 'O', 'å': 'a', 'Å': 'A', 'æ': 'ae', 'Æ': 'AE',
            
            # Common Cyrillic (basic mapping)
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
            'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh',
            'щ': 'sch', 'ы': 'y', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        }
        
        # Apply character mappings
        result = no_accents
        for original, replacement in char_map.items():
            result = result.replace(original, replacement)
        
        # Remove any remaining non-ASCII characters
        result = ''.join(char for char in result if ord(char) < 128)
        
        return result
    
    def sanitize_filename(self, filename: str) -> str:
        """Remove or replace characters that are not allowed in filenames."""
        # First, convert non-Latin characters to Latin equivalents
        transliterated = self.transliterate_to_latin(filename)
        
        # Handle colon specifically with configurable replacement
        sanitized = transliterated.replace(':', self.config['COLON_REPLACEMENT'])
        
        # Replace other problematic characters with spaces
        sanitized = re.sub(r'[<>"/\\|?*]', ' ', sanitized)
        
        # Remove control characters and other problematic chars
        sanitized = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', sanitized)
        
        # Clean up multiple spaces and trim
        sanitized = re.sub(r'\s+', ' ', sanitized)
        sanitized = sanitized.strip('. ')
        
        # Ensure filename isn't empty after sanitization
        return sanitized if sanitized else "UnknownFile"
    
    def fix_caps(self, text: str) -> str:
        """Convert ALL UPPERCASE text to proper Title Case for better readability."""
        if not text:
            return text
            
        # Only convert if the entire string is uppercase
        if text.isupper():
            # Use title() but handle common exceptions
            result = text.title()
            
            # Fix common title case issues
            small_words = ['of', 'the', 'and', 'in', 'on', 'a', 'an', 'to', 'for', 'with', 'at', 'by', 'from']
            for word in small_words:
                result = re.sub(rf'\b{word.title()}\b', word, result)
            
            # Ensure first word is always capitalized
            if result and result[0].islower():
                result = result[0].upper() + result[1:]
            
            return result
        
        return text
    
    def clean_author_name(self, name: str) -> str:
        """Clean and format author name for filename use. Returns only the last name."""
        if not name:
            return "UnknownAuthor"
        
        # Fix caps and basic cleaning
        cleaned = self.fix_caps(name.strip())
        
        # Remove common prefixes/suffixes
        prefixes_to_remove = ['Dr.', 'Prof.', 'Professor', 'Mr.', 'Ms.', 'Mrs.']
        suffixes_to_remove = ['Ph.D.', 'PhD', 'M.D.', 'MD', 'Jr.', 'Sr.', 'III', 'II']
        
        for prefix in prefixes_to_remove:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
        
        for suffix in suffixes_to_remove:
            if cleaned.endswith(suffix):
                cleaned = cleaned[:-len(suffix)].strip()
        
        # Handle "Last, First" format
        if ',' in cleaned:
            parts = cleaned.split(',', 1)
            if len(parts) >= 1:
                return parts[0].strip()
        
        # Handle "First Last" or "First Middle Last" format
        name_parts = cleaned.split()
        if len(name_parts) > 1:
            return name_parts[-1]
        elif len(name_parts) == 1:
            return name_parts[0]
        
        return "UnknownAuthor"
    
    def extract_doi_from_text(self, text: str) -> Optional[str]:
        """Extract DOI from text using regex pattern."""
        # Standard DOI pattern
        doi_pattern = r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b'
        
        match = re.search(doi_pattern, text, re.IGNORECASE)
        if match:
            doi = match.group(0).rstrip('.,;')
            return doi
        
        # Also check for DOI with explicit prefix
        doi_prefix_pattern = r'doi:\s*(\b10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b'
        match = re.search(doi_prefix_pattern, text, re.IGNORECASE)
        if match:
            doi = match.group(1).rstrip('.,;')
            return doi
        
        # Check for Frontiers-style DOI in the filename or text
        # Pattern like "feduc-2021-667869" might indicate DOI 10.3389/feduc.2021.667869
        frontiers_pattern = r'\b(f[a-z]+)-(\d{4})-(\d{6})\b'
        match = re.search(frontiers_pattern, text, re.IGNORECASE)
        if match:
            journal = match.group(1)
            year = match.group(2)
            article_id = match.group(3)
            # Construct the DOI
            doi = f"10.3389/{journal}.{year}.{article_id}"
            return doi
        
        return None
    
    def extract_arxiv_from_text(self, text: str) -> Optional[str]:
        """Extract arXiv ID from text."""
        # Modern arXiv format: YYMM.NNNNN or YYMM.NNNN
        arxiv_pattern = r'(?:arXiv:)?(\d{4}\.\d{4,5})'
        
        match = re.search(arxiv_pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Legacy arXiv format: category/YYMMNNN
        legacy_pattern = r'(?:arXiv:)?([a-z\-]+/\d{7})'
        match = re.search(legacy_pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
        
        return None
    
    def extract_isbn_from_text(self, text: str) -> Optional[str]:
        """Extract ISBN from text."""
        clean_text = text.replace('\n', ' ').replace('\r', ' ')
        
        # Pattern for ISBN
        isbn_pattern = r'ISBN[-‐–]?(1[03])?:?\s*(97[89])?\d{9}[\dX]'
        
        match = re.search(isbn_pattern, clean_text, re.IGNORECASE)
        if match:
            isbn_clean = re.sub(r'[^0-9X]', '', match.group(0).upper())
            
            if len(isbn_clean) >= 13:
                return isbn_clean[-13:]
            elif len(isbn_clean) >= 10:
                return isbn_clean[-10:]
        
        return None
    
    def extract_title_from_text(self, text: str) -> Optional[str]:
        """Try to extract title from the first page of PDF text."""
        # Clean up common PDF extraction issues
        text = text.replace('\x00', '').replace('\xa0', ' ')
        
        # Remove URLs that might be embedded in the text
        text = re.sub(r'https?://[^\s]+', '', text)
        text = re.sub(r'www\.[^\s]+', '', text)
        
        lines = text.split('\n')
        
        # Look for common title patterns
        potential_titles = []
        
        for i, line in enumerate(lines[:30]):  # Check first 30 lines
            line = line.strip()
            
            # Clean up the line from common PDF artifacts
            line = re.sub(r'\s+', ' ', line)
            
            # Skip empty lines and very short lines
            if len(line) < 10:
                continue
            
            # Skip lines that look like headers/footers/metadata
            skip_patterns = [
                'page', 'vol.', 'no.', 'journal', 'copyright', 'doi:', 
                'issn', 'isbn', 'published', 'received', 'accepted',
                'edited by', 'reviewed by', 'keywords', 'abstract',
                'article', 'original research', 'brief report',
                'front. educ', 'frontiers in', 'www.frontiersin.org'
            ]
            if any(skip in line.lower() for skip in skip_patterns):
                continue
            
            # Skip lines that look like article IDs or technical metadata
            if re.match(r'^[a-z]+-\d{4}-\d{6}', line.lower()):
                continue
            
            # Skip lines that are mostly numbers or special characters
            if len(line) > 0 and sum(c.isdigit() or c in '()[]{}' for c in line) / len(line) > 0.3:
                continue
            
            # Look for title characteristics
            if 20 < len(line) < 200 and not line.endswith(':'):
                # Check if it might be a title
                words = line.split()
                if len(words) >= 3:  # At least 3 words
                    # Count capitalized words
                    cap_ratio = sum(1 for w in words if w and w[0].isupper()) / len(words)
                    if cap_ratio > 0.5:  # More than half the words are capitalized
                        potential_titles.append((i, line, cap_ratio))
        
        # Return the best candidate
        if potential_titles:
            potential_titles.sort(key=lambda x: (x[0], -x[2]))
            return potential_titles[0][1]
        
        return None
    
    def extract_ids_from_pdf(self, reader: PdfReader, max_pages: int = None) -> Dict[str, str]:
        """Search through PDF pages to find all available identifiers."""
        if max_pages is None:
            max_pages = self.config['MAX_PAGES_TO_SEARCH']
        
        pages_to_check = min(max_pages, len(reader.pages))
        
        ids = {}
        
        for page_num in range(pages_to_check):
            try:
                # Extract text with better handling of formatting
                page = reader.pages[page_num]
                text = page.extract_text()
                
                # Sometimes we need to try alternative extraction methods
                if not text or len(text.strip()) < 50:
                    try:
                        text = page.extract_text(extraction_mode="layout")
                    except:
                        pass
                
                if text:
                    # Clean up text
                    text = text.replace('\x00', '').replace('\xa0', ' ')
                    
                    # Look for DOI
                    if 'doi' not in ids:
                        doi = self.extract_doi_from_text(text)
                        if doi:
                            ids['doi'] = doi
                    
                    # Look for arXiv ID
                    if 'arxiv' not in ids and self.config['USE_ARXIV']:
                        arxiv = self.extract_arxiv_from_text(text)
                        if arxiv:
                            ids['arxiv'] = arxiv
                    
                    # Look for ISBN
                    if 'isbn' not in ids:
                        isbn = self.extract_isbn_from_text(text)
                        if isbn:
                            ids['isbn'] = isbn
                    
                    # Try to extract title from first page
                    if page_num == 0 and 'title' not in ids:
                        title = self.extract_title_from_text(text)
                        if title:
                            ids['title'] = title
                    
                    # If we found identifiers, we can stop
                    if any(k in ids for k in ['doi', 'arxiv', 'isbn']):
                        break
                        
            except Exception as e:
                self.log('warning', f"Error extracting text from page {page_num + 1}: {e}")
                continue
        
        return ids
    
    def query_crossref(self, doi: str) -> Dict[str, Any]:
        """Query CrossRef API to get metadata for a given DOI."""
        cache_key = f"crossref_{doi}"
        if cache_key in self.api_cache:
            return self.api_cache[cache_key]
        
        url = f"https://api.crossref.org/works/{doi}"
        
        headers = {
            'User-Agent': 'PDF-Renamer-Script/4.0',
            'Accept': 'application/json'
        }
        
        try:
            self.log('info', f"Querying CrossRef for DOI: {doi}")
            
            response = requests.get(url, headers=headers, timeout=self.config['API_TIMEOUT'])
            
            if response.status_code == 200:
                data = response.json()
                result = data.get("message", {})
                self.api_cache[cache_key] = result
                return result
            elif response.status_code == 404:
                self.log('warning', f"DOI not found in CrossRef: {doi}")
            else:
                self.log('warning', f"CrossRef API returned status {response.status_code} for DOI: {doi}")
                
        except requests.exceptions.Timeout:
            self.log('error', f"CrossRef API timeout for DOI: {doi}")
        except Exception as e:
            self.log('error', f"CrossRef lookup failed for DOI {doi}: {e}")
        
        self.api_cache[cache_key] = {}
        return {}
    
    def query_semantic_scholar(self, identifier: str, id_type: str = 'doi') -> Dict[str, Any]:
        """Query Semantic Scholar API for metadata."""
        if not self.config['USE_SEMANTIC_SCHOLAR']:
            return {}
        
        cache_key = f"s2_{id_type}_{identifier}"
        if cache_key in self.api_cache:
            return self.api_cache[cache_key]
        
        # Semantic Scholar accepts DOI, arXiv ID, or title
        if id_type == 'doi':
            url = f"https://api.semanticscholar.org/graph/v1/paper/{identifier}"
        elif id_type == 'arxiv':
            url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{identifier}"
        elif id_type == 'title':
            # Search by title
            url = "https://api.semanticscholar.org/graph/v1/paper/search"
            params = {'query': identifier, 'limit': 1}
        else:
            return {}
        
        headers = {
            'User-Agent': 'PDF-Renamer-Script/4.0',
            'Accept': 'application/json'
        }
        
        try:
            self.log('info', f"Querying Semantic Scholar for {id_type}: {identifier[:50]}...")
            
            if id_type == 'title':
                response = requests.get(url, headers=headers, params=params, timeout=self.config['API_TIMEOUT'])
            else:
                response = requests.get(url, headers=headers, timeout=self.config['API_TIMEOUT'])
            
            if response.status_code == 200:
                data = response.json()
                
                if id_type == 'title' and 'data' in data and data['data']:
                    # Check if title is similar enough
                    result = data['data'][0]
                    similarity = SequenceMatcher(None, identifier.lower(), result.get('title', '').lower()).ratio()
                    if similarity >= self.config['FUZZY_MATCH_THRESHOLD']:
                        self.api_cache[cache_key] = result
                        return result
                elif id_type != 'title':
                    self.api_cache[cache_key] = data
                    return data
            else:
                self.log('warning', f"Semantic Scholar API returned status {response.status_code}")
                
        except Exception as e:
            self.log('error', f"Semantic Scholar lookup failed: {e}")
        
        self.api_cache[cache_key] = {}
        return {}
    
    def query_arxiv(self, arxiv_id: str) -> Dict[str, Any]:
        """Query arXiv API for metadata."""
        if not self.config['USE_ARXIV']:
            return {}
        
        cache_key = f"arxiv_{arxiv_id}"
        if cache_key in self.api_cache:
            return self.api_cache[cache_key]
        
        url = "http://export.arxiv.org/api/query"
        params = {
            'id_list': arxiv_id,
            'max_results': 1
        }
        
        try:
            self.log('info', f"Querying arXiv for ID: {arxiv_id}")
            
            response = requests.get(url, params=params, timeout=self.config['API_TIMEOUT'])
            
            if response.status_code == 200:
                # Parse XML response
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.text)
                
                # Extract metadata from XML
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                entry = root.find('atom:entry', ns)
                
                if entry is not None:
                    result = {
                        'title': entry.find('atom:title', ns).text.strip() if entry.find('atom:title', ns) is not None else None,
                        'authors': [author.find('atom:name', ns).text for author in entry.findall('atom:author', ns)],
                        'published': entry.find('atom:published', ns).text if entry.find('atom:published', ns) is not None else None,
                    }
                    self.api_cache[cache_key] = result
                    return result
                    
        except Exception as e:
            self.log('error', f"arXiv lookup failed: {e}")
        
        self.api_cache[cache_key] = {}
        return {}
    
    def query_openlibrary(self, isbn: str) -> Dict[str, Any]:
        """Query Open Library API to get metadata for a given ISBN."""
        cache_key = f"openlibrary_{isbn}"
        if cache_key in self.api_cache:
            return self.api_cache[cache_key]
        
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        
        try:
            self.log('info', f"Querying Open Library for ISBN: {isbn}")
            
            response = requests.get(url, timeout=self.config['API_TIMEOUT'])
            
            if response.status_code == 200:
                data = response.json()
                book_data = data.get(f"ISBN:{isbn}", {})
                
                if book_data:
                    self.api_cache[cache_key] = book_data
                    return book_data
                else:
                    self.log('warning', f"No data found for ISBN: {isbn}")
            else:
                self.log('warning', f"Open Library API returned status {response.status_code} for ISBN: {isbn}")
                
        except requests.exceptions.Timeout:
            self.log('error', f"Open Library API timeout for ISBN: {isbn}")
        except Exception as e:
            self.log('error', f"Open Library lookup failed for ISBN {isbn}: {e}")
        
        self.api_cache[cache_key] = {}
        return {}
    
    def extract_author_from_crossref(self, authors_data: list) -> str:
        """Extract author name from CrossRef API response."""
        if not authors_data:
            return "UnknownAuthor"
        
        first_author = authors_data[0]
        family = first_author.get("family", "")
        
        if family:
            return family
        else:
            given = first_author.get("given", "")
            return given if given else "UnknownAuthor"
    
    def extract_author_from_semantic_scholar(self, authors_data: list) -> str:
        """Extract author name from Semantic Scholar API response."""
        if not authors_data:
            return "UnknownAuthor"
        
        first_author = authors_data[0]
        if isinstance(first_author, dict):
            name = first_author.get("name", "")
        else:
            name = str(first_author)
        
        if name:
            return self.clean_author_name(name)
        
        return "UnknownAuthor"
    
    def extract_author_from_arxiv(self, authors_data: list) -> str:
        """Extract author name from arXiv API response."""
        if not authors_data:
            return "UnknownAuthor"
        
        first_author = authors_data[0]
        if first_author:
            return self.clean_author_name(first_author)
        
        return "UnknownAuthor"
    
    def extract_author_from_openlibrary(self, authors_data: list) -> str:
        """Extract author name from Open Library API response."""
        if not authors_data:
            return "UnknownAuthor"
        
        first_author_name = authors_data[0].get("name", "")
        if first_author_name:
            return self.clean_author_name(first_author_name)
        
        return "UnknownAuthor"
    
    def extract_year_from_crossref(self, date_info: dict) -> Optional[str]:
        """Extract publication year from CrossRef date information."""
        date_fields = ['published-print', 'published-online', 'created', 'deposited']
        
        for field in date_fields:
            if field in date_info:
                date_parts = date_info[field].get('date-parts', [])
                if date_parts and date_parts[0] and date_parts[0][0]:
                    year = date_parts[0][0]
                    if isinstance(year, int) and 1000 <= year <= 9999:
                        return str(year)
        
        return None
    
    def extract_year_from_date_string(self, date_str: str) -> Optional[str]:
        """Extract year from a date string."""
        if not date_str:
            return None
        
        # Try to find a 4-digit year
        year_match = re.search(r'\b(19|20)\d{2}\b', date_str)
        if year_match:
            return year_match.group(0)
        
        return None
    
    def improved_title_extraction(self, raw_title: str) -> str:
        """Better title cleaning that preserves readability."""
        if not raw_title:
            return "UnknownTitle"
        
        # Check if this looks like a technical identifier rather than a real title
        if re.match(r'^[a-z]+-\d{4}-\d{6}', raw_title.lower()):
            self.log('info', f"Title looks like technical ID, ignoring: {raw_title}")
            return "UnknownTitle"
        
        title = self.fix_caps(raw_title.strip())
        
        # Remove common unwanted patterns
        title = re.sub(r'^(Microsoft Word - |Adobe PDF - )', '', title, flags=re.IGNORECASE)
        if title.lower().endswith('.pdf'):
            title = title[:-4]
        
        # Remove "Edited by:", "Reviewed by:", etc. that often appear at the end
        for phrase in ['Edited by', 'Reviewed by', 'Copyright', 'doi', 'DOI']:
            idx = title.lower().find(phrase.lower())
            if idx > 0:
                title = title[:idx].strip()
        
        # Remove excessive whitespace
        title = re.sub(r'\s+', ' ', title)
        
        return title.strip() if title.strip() else "UnknownTitle"
    
    def create_filename(self, author: str, year: str, title: str) -> str:
        """Create a standardized filename from metadata components."""
        author_clean = self.sanitize_filename(author)
        year_clean = self.sanitize_filename(year)
        title_clean = self.sanitize_filename(title)
        
        # Remove trailing punctuation that might look odd
        title_clean = title_clean.rstrip('.,;:!?')
        
        # Truncate title if too long, but ensure clean ending
        if len(title_clean) > self.config['MAX_TITLE_LENGTH']:
            # Try to find a good break point (space) before the limit
            truncate_pos = title_clean.rfind(' ', 0, self.config['MAX_TITLE_LENGTH'])
            
            if truncate_pos > self.config['MAX_TITLE_LENGTH'] * 0.7:
                title_clean = title_clean[:truncate_pos]
            else:
                # If no good space found, just truncate
                title_clean = title_clean[:self.config['MAX_TITLE_LENGTH']]
            
            # Clean up the truncated title
            title_clean = title_clean.rstrip()
            # Remove any trailing punctuation or partial words
            title_clean = re.sub(r'[,;:\-\s]+$', '', title_clean)
            # Remove last word if it looks incomplete (ends with lowercase letter)
            words = title_clean.split()
            if words and len(words[-1]) > 2 and words[-1][-1].islower() and words[-1] not in ['a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for']:
                words = words[:-1]
                title_clean = ' '.join(words)
        
        filename = f"{author_clean} - {year_clean} - {title_clean}.pdf"
        
        return filename
    
    def handle_duplicate_filenames(self, target_path: str, original_path: str) -> str:
        """Handle duplicate filenames by adding incrementing numbers."""
        # If target is same as original, no need to rename
        if os.path.abspath(target_path) == os.path.abspath(original_path):
            return target_path
        
        if not os.path.exists(target_path):
            return target_path
        
        base_path, ext = os.path.splitext(target_path)
        counter = 1
        
        while True:
            new_path = f"{base_path} ({counter}){ext}"
            if not os.path.exists(new_path) or os.path.abspath(new_path) == os.path.abspath(original_path):
                return new_path
            counter += 1
    
    def backup_file(self, file_path: str) -> bool:
        """Create a backup of the file before renaming."""
        try:
            backup_dir = os.path.join(os.path.dirname(file_path), self.config['BACKUP_FOLDER'])
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.basename(file_path)
            backup_path = os.path.join(backup_dir, f"{timestamp}_{filename}")
            
            shutil.copy2(file_path, backup_path)
            self.log('info', f"Created backup: {backup_path}")
            return True
            
        except Exception as e:
            self.log('error', f"Failed to create backup: {e}")
            return False
    
    def move_to_needs_attention(self, file_path: str, problem_folder: str, reason: str = "unknown") -> Tuple[str, str]:
        """Move a file to the needs-attention folder."""
        try:
            os.makedirs(problem_folder, exist_ok=True)
            filename = os.path.basename(file_path)
            
            # Keep original filename but handle duplicates
            new_path = os.path.join(problem_folder, filename)
            new_path = self.handle_duplicate_filenames(new_path, file_path)
            
            shutil.move(file_path, new_path)
            self.log('info', f"Moved to needs-attention ({reason}): {filename}")
            
            return os.path.basename(new_path), f"⚠ Moved to needs-attention ({reason})"
            
        except Exception as e:
            self.log('error', f"Failed to move file to needs-attention: {e}")
            raise
    
    def process_single_pdf(self, file_path: str, problem_folder: str) -> Tuple[bool, str, str, bool]:
        """
        Process a single PDF file: extract metadata, enrich if needed, and rename.
        
        Returns:
            Tuple[bool, str, str, bool]: (success, status_message, new_filename, needs_attention)
        """
        filename = os.path.basename(file_path)
        needs_attention = False
        
        if self.config['DRY_RUN']:
            self.log('info', f"[DRY RUN] Would process: {filename}")
        
        try:
            # Create backup first
            if not self.config['DRY_RUN'] and self.config['CREATE_BACKUP']:
                if not self.backup_file(file_path):
                    # If backup fails and we're not in dry run, move to needs-attention
                    if not self.config['DRY_RUN']:
                        new_filename, status = self.move_to_needs_attention(file_path, problem_folder, "backup_failed")
                        return False, status, new_filename, True
                    return False, "Backup failed", filename, False
            
            # Open PDF and extract basic metadata
            reader = PdfReader(file_path)
            metadata = reader.metadata or {}
            
            self.log('info', f"Processing: {filename}")
            
            # Extract raw metadata from PDF
            raw_author = metadata.get('/Author')
            raw_title = metadata.get('/Title')
            raw_date = metadata.get('/CreationDate') or metadata.get('/ModDate')
            
            self.log('debug', f"Raw metadata - Author: {raw_author}, Title: {raw_title}, Date: {raw_date}")
            
            # Search for identifiers in PDF content
            ids = self.extract_ids_from_pdf(reader)
            self.log('info', f"Extracted IDs from PDF: {ids}")
            
            # Try to enrich missing metadata through external APIs
            enriched_data = {}
            metadata_source = "pdf_metadata"
            
            # Try different APIs in order of preference
            if ids.get('doi'):
                self.log('info', f"Found DOI: {ids['doi']}")
                time.sleep(self.config['API_DELAY'])
                
                # Try CrossRef first
                enriched_data = self.query_crossref(ids['doi'])
                if enriched_data:
                    metadata_source = "doi_crossref"
                
                # If CrossRef didn't provide complete data, try Semantic Scholar
                if not enriched_data or not all(k in enriched_data for k in ['author', 'title']):
                    time.sleep(self.config['API_DELAY'])
                    s2_data = self.query_semantic_scholar(ids['doi'], 'doi')
                    if s2_data:
                        enriched_data.update(s2_data)
                        metadata_source = "doi_semantic_scholar"
            
            elif ids.get('arxiv'):
                self.log('info', f"Found arXiv ID: {ids['arxiv']}")
                time.sleep(self.config['API_DELAY'])
                
                # Try arXiv API
                enriched_data = self.query_arxiv(ids['arxiv'])
                if enriched_data:
                    metadata_source = "arxiv"
                
                # Also try Semantic Scholar with arXiv ID
                if not enriched_data or not all(k in enriched_data for k in ['authors', 'title']):
                    time.sleep(self.config['API_DELAY'])
                    s2_data = self.query_semantic_scholar(ids['arxiv'], 'arxiv')
                    if s2_data:
                        enriched_data.update(s2_data)
                        metadata_source = "arxiv_semantic_scholar"
            
            elif ids.get('isbn'):
                self.log('info', f"Found ISBN: {ids['isbn']}")
                time.sleep(self.config['API_DELAY'])
                enriched_data = self.query_openlibrary(ids['isbn'])
                if enriched_data:
                    metadata_source = "isbn_openlibrary"
            
            else:
                # No identifiers found - try title search if we have a title
                self.log('info', "No identifiers found in PDF")
                needs_attention = True  # Always needs attention if no identifiers
                
                if ids.get('title'):
                    self.log('info', f"Trying Semantic Scholar title search: {ids['title'][:50]}...")
                    time.sleep(self.config['API_DELAY'])
                    enriched_data = self.query_semantic_scholar(ids['title'], 'title')
                    if enriched_data:
                        metadata_source = "title_search"
            
            # Process and clean metadata
            final_author = "UnknownAuthor"
            final_title = "UnknownTitle"
            final_year = "UnknownYear"
            
            # Process author information (priority: API data > PDF metadata)
            if enriched_data:
                if 'author' in enriched_data:
                    final_author = self.extract_author_from_crossref(enriched_data['author'])
                elif 'authors' in enriched_data:
                    if enriched_data['authors'] and isinstance(enriched_data['authors'][0], dict):
                        final_author = self.extract_author_from_semantic_scholar(enriched_data['authors'])
                    else:
                        final_author = self.extract_author_from_arxiv(enriched_data['authors'])
            elif raw_author:
                final_author = self.clean_author_name(raw_author)
            
            # Process title information
            if enriched_data and 'title' in enriched_data:
                if isinstance(enriched_data['title'], list):
                    full_title = enriched_data['title'][0] if enriched_data['title'] else "UnknownTitle"
                else:
                    full_title = enriched_data['title']
                
                # For very long titles with colons, keep the full title
                final_title = self.improved_title_extraction(full_title)
                self.log('info', f"Using API title: {full_title[:50]}... -> {final_title[:50]}...")
            elif raw_title:
                final_title = self.improved_title_extraction(raw_title)
                self.log('info', f"Using PDF metadata title: {raw_title[:50]}... -> {final_title[:50]}...")
            elif ids.get('title'):
                final_title = self.improved_title_extraction(ids['title'])
                self.log('info', f"Using extracted title: {ids['title'][:50]}... -> {final_title[:50]}...")
            
            # Process year information
            if enriched_data:
                if 'published-print' in enriched_data or 'published-online' in enriched_data:
                    final_year = self.extract_year_from_crossref(enriched_data) or "UnknownYear"
                elif 'year' in enriched_data:
                    final_year = str(enriched_data['year'])
                elif 'published' in enriched_data:
                    final_year = self.extract_year_from_date_string(enriched_data['published']) or "UnknownYear"
                elif 'publish_date' in enriched_data:
                    final_year = self.extract_year_from_date_string(enriched_data['publish_date']) or "UnknownYear"
            elif raw_date:
                if raw_date.startswith('D:'):
                    year_match = re.search(r'D:(\d{4})', raw_date)
                    if year_match:
                        final_year = year_match.group(1)
                else:
                    year_match = re.search(r'\b(19|20)\d{2}\b', str(raw_date))
                    if year_match:
                        final_year = year_match.group(0)
            
            # Log the metadata source
            self.log('info', f"Metadata source: {metadata_source}")
            
            # Check if any field is unknown - if so, needs attention
            if any(field == f"Unknown{name}" for field, name in [(final_author, "Author"), (final_title, "Title"), (final_year, "Year")]):
                needs_attention = True
            
            # Create new filename
            new_filename = self.create_filename(final_author, final_year, final_title)
            
            if not self.config['DRY_RUN']:
                if needs_attention:
                    # Move to problem folder
                    os.makedirs(problem_folder, exist_ok=True)
                    new_path = os.path.join(problem_folder, new_filename)
                    new_path = self.handle_duplicate_filenames(new_path, file_path)
                    shutil.move(file_path, new_path)
                    status = f"⚠ Moved to needs-attention ({metadata_source})"
                else:
                    # Rename in place
                    new_path = os.path.join(os.path.dirname(file_path), new_filename)
                    new_path = self.handle_duplicate_filenames(new_path, file_path)
                    if os.path.abspath(new_path) != os.path.abspath(file_path):
                        os.rename(file_path, new_path)
                    status = f"✓ Renamed ({metadata_source})"
            else:
                if needs_attention:
                    status = f"⚠ Would move to needs-attention ({metadata_source})"
                else:
                    status = f"✓ Would rename ({metadata_source})"
            
            return True, status, new_filename, needs_attention
            
        except Exception as e:
            self.log('error', f"Failed to process '{filename}': {e}")
            
            # Move failed files to needs-attention if not in dry run mode
            if not self.config['DRY_RUN']:
                try:
                    new_filename, status = self.move_to_needs_attention(file_path, problem_folder, "processing_error")
                    return False, status, new_filename, True
                except Exception as move_error:
                    self.log('error', f"Failed to move file after error: {move_error}")
                    return False, f"✗ Error: {str(e)} (couldn't move to needs-attention)", filename, False
            else:
                return False, f"✗ Would fail and move to needs-attention: {str(e)}", filename, False
    
    def process_directory(self, directory: str = None) -> Dict[str, int]:
        """
        Process all PDF files in the specified directory.
        
        Args:
            directory (str): Directory to process. Uses current directory if None.
            
        Returns:
            Dict[str, int]: Statistics about the processing
        """
        if directory is None:
            directory = os.getcwd()
        
        print(f"\nProcessing PDFs in: {directory}")
        print(f"Always creating backups in: {self.config['BACKUP_FOLDER']}")
        
        if self.config['DRY_RUN']:
            print("DRY RUN MODE - No files will be moved or renamed\n")
        
        # Set up problem folder
        problem_folder = os.path.join(directory, self.config['PROBLEM_FOLDER'])
        
        # Find all PDF files
        pdf_files = []
        for file in os.listdir(directory):
            if file.lower().endswith('.pdf'):
                file_path = os.path.join(directory, file)
                # Skip files in problem folder or backup folder
                if (self.config['PROBLEM_FOLDER'] not in file_path and 
                    self.config['BACKUP_FOLDER'] not in file_path):
                    pdf_files.append(file_path)
        
        if not pdf_files:
            print("No PDF files found!")
            return self.stats
        
        print(f"Found {len(pdf_files)} PDF files to process\n")
        
        # Process each PDF file
        if TQDM_AVAILABLE:
            pdf_iterator = tqdm(pdf_files, desc="Processing PDFs", unit="file")
        else:
            pdf_iterator = pdf_files
        
        results = []
        
        for file_path in pdf_iterator:
            success, status, new_name, needs_attention = self.process_single_pdf(file_path, problem_folder)
            
            if success:
                if needs_attention:
                    self.stats['needs_attention'] += 1
                else:
                    self.stats['successful'] += 1
            else:
                # Failed files that were moved to needs-attention are counted there
                if needs_attention:
                    self.stats['needs_attention'] += 1
                else:
                    self.stats['failed'] += 1
            
            self.stats['processed'] += 1
            
            # Store result for summary
            results.append({
                'original': os.path.basename(file_path),
                'new': new_name,
                'status': status,
                'success': success,
                'needs_attention': needs_attention
            })
            
            # Update progress bar description if available
            if TQDM_AVAILABLE:
                pdf_iterator.set_postfix({
                    'Success': self.stats['successful'],
                    'Attention': self.stats['needs_attention'],
                    'Failed': self.stats['failed']
                })
        
        # Print summary
        self._print_summary(results)
        
        return self.stats
    
    def _print_summary(self, results: List[Dict[str, Any]]):
        """Print a summary of the processing results."""
        print("\n" + "="*80)
        print("PROCESSING COMPLETE")
        print("="*80)
        
        # Overall statistics
        print(f"\nTotal processed: {self.stats['processed']}")
        print(f"✓ Successfully renamed: {self.stats['successful']}")
        print(f"⚠ Moved to '{self.config['PROBLEM_FOLDER']}': {self.stats['needs_attention']}")
        print(f"✗ Failed (not moved): {self.stats['failed']}")
        
        if self.stats['needs_attention'] > 0:
            print(f"\nFiles needing verification have been moved to: {self.config['PROBLEM_FOLDER']}/")
            print("These files either:")
            print("  - Had no DOI/ISBN/arXiv ID found")
            print("  - Have incomplete metadata")
            print("  - Failed during processing")
            print("Please review and verify these files manually.")
        
        if self.config['CREATE_BACKUP'] and not self.config['DRY_RUN']:
            print(f"\nBackups created in: {self.config['BACKUP_FOLDER']}/")
            print("You can manually delete backups after verifying successful renames.")
        
        # Detailed results
        if self.config['ENABLE_LOGGING']:
            print(f"\nDetailed log available in: {self.config['LOG_FILE']}")
        else:
            print("\n" + "-"*80)
            print("DETAILED RESULTS:")
            print("-"*80)
            
            for result in results:
                print(f"{result['status']} {result['original']} → {result['new']}")


def main():
    """Main function with command-line interface."""
    parser = argparse.ArgumentParser(
        description="Rename PDF files based on metadata (author, year, title) - v4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This version prioritizes reliable metadata sources:
1. DOI/ISBN/arXiv lookups (most trusted)
2. Semantic Scholar title search (if no identifiers)
3. PDF internal metadata (fallback only)

Files without identifiers or that fail processing are moved to needs-attention folder.

Examples:
  %(prog)s                     # Process PDFs in current directory
  %(prog)s -d /path/to/pdfs    # Process PDFs in specified directory
  %(prog)s --dry-run           # Test without making changes
  %(prog)s --log               # Enable detailed logging
  %(prog)s --colon-replace "_" # Replace colons with underscores
        """
    )
    
    parser.add_argument('-d', '--directory', type=str, default=None,
                        help='Directory containing PDFs (default: current directory)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Test mode - show what would be done without making changes')
    parser.add_argument('--log', action='store_true',
                        help='Enable detailed logging to file')
    parser.add_argument('--colon-replace', type=str, default=' -',
                        help='What to replace colons with in filenames (default: " -")')
    parser.add_argument('--no-semantic-scholar', action='store_true',
                        help='Disable Semantic Scholar API lookups')
    parser.add_argument('--no-arxiv', action='store_true',
                        help='Disable arXiv API lookups')
    parser.add_argument('--config', type=str,
                        help='JSON config file to override default settings')
    
    args = parser.parse_args()
    
    # Load configuration
    config = CONFIG.copy()
    
    # Override with config file if provided
    if args.config:
        try:
            with open(args.config, 'r') as f:
                custom_config = json.load(f)
                config.update(custom_config)
                print(f"Loaded configuration from: {args.config}")
        except Exception as e:
            print(f"Warning: Could not load config file: {e}")
    
    # Override with command-line arguments
    if args.dry_run:
        config['DRY_RUN'] = True
    if args.log:
        config['ENABLE_LOGGING'] = True
    if args.colon_replace:
        config['COLON_REPLACEMENT'] = args.colon_replace
    if args.no_semantic_scholar:
        config['USE_SEMANTIC_SCHOLAR'] = False
    if args.no_arxiv:
        config['USE_ARXIV'] = False
    
    # Create and run the renamer
    renamer = PDFRenamer(config)
    renamer.process_directory(args.directory)


if __name__ == '__main__':
    main()
