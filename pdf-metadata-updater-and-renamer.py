import os
import re
import time
import requests
from pypdf import PdfReader, PdfWriter

# Function to sanitize filenames by replacing invalid characters and truncate without cutting words
def sanitize_filename(filename, max_length=150):
    filename = re.sub(r'[<>:"/\\|?*]', '_', str(filename))
    name, extension = os.path.splitext(filename)
    
    if len(filename) > max_length:
        max_name_length = max_length - len(extension)
        truncated = name[:max_name_length].rstrip('_')
        last_space = truncated.rfind(' ')
        if last_space != -1:
            name = truncated[:last_space]
        else:
            name = truncated
    
    return f"{name}{extension}"

# Function to remove HTML tags and replace them with a space
def strip_html_tags(text):
    clean = re.compile('<.*?>')
    return re.sub(clean, ' ', text)

# Function to get metadata from CrossRef using the title
def get_metadata_from_crossref(title):
    query = title.replace(' ', '+')
    headers = {
        'User-Agent': 'MyPDFMetadataUpdater/1.0 (your-email@example.com)'
    }
    response = requests.get(f"https://api.crossref.org/works?query.title={query}", headers=headers)
    if response.status_code == 200:
        data = response.json()
        if data['message']['items']:
            item = data['message']['items'][0]
            metadata = {
                'author': ', '.join([str(author['family']) for author in item.get('author', [])]),
                'title': strip_html_tags(str(item.get('title', ['UnknownTitle'])[0])),
                'year': str(item.get('published-print', {}).get('date-parts', [[None]])[0][0] or 'UnknownYear')
            }
            return metadata
    return None

# Function to update PDF metadata and rename the file
def update_and_rename_pdf(file_path, new_metadata):
    reader = PdfReader(file_path)
    writer = PdfWriter()

    writer.append_pages_from_reader(reader)
    writer.add_metadata({
        '/Author': new_metadata.get('author', 'UnknownAuthor'),
        '/Title': new_metadata.get('title', 'UnknownTitle'),
        '/CreationDate': f"D:{new_metadata.get('year', '0000')}"
    })

    author_last_name = sanitize_filename(new_metadata.get('author', 'UnknownAuthor').split(',')[0])
    title = sanitize_filename(new_metadata.get('title', 'UnknownTitle'))
    year = sanitize_filename(new_metadata.get('year', 'UnknownYear'))
    new_filename = sanitize_filename(f"{author_last_name} - {year} - {title}.pdf")

    new_file_path = os.path.join(os.path.dirname(file_path), new_filename)
    with open(new_file_path, 'wb') as f_out:
        writer.write(f_out)
    print(f"Updated and renamed PDF saved as '{new_filename}'")

# Directory containing your PDFs
input_folder = 'folder_to_rename'

# Iterate over all PDF files in the directory
for filename in os.listdir(input_folder):
    if filename.endswith('.pdf'):
        file_path = os.path.join(input_folder, filename)
        try:
            reader = PdfReader(file_path)
            current_metadata = reader.metadata
            title = current_metadata.get('/Title', None)

            if title:
                print(f"Fetching metadata for '{title}'...")
                new_metadata = get_metadata_from_crossref(title)
                
                if new_metadata:
                    update_and_rename_pdf(file_path, new_metadata)
                    time.sleep(1)  # Adjust as needed for polite use
                else:
                    print(f"No metadata found for '{title}'. Skipping.")
            else:
                print(f"No title found in metadata for '{filename}'. Skipping.")

        except Exception as e:
            print(f"Failed to process '{filename}': {e}")
