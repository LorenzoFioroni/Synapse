import sqlite3
import json
import os
from flask import Flask, jsonify, request, send_from_directory, abort

app = Flask(__name__, static_folder='.')

DB_FILE = 'papers.db'
PDF_DIR = 'pdf_storage'

os.makedirs(PDF_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,
            title TEXT,
            authors TEXT,
            year INTEGER,
            journal TEXT,
            abstract TEXT,
            pdfUrl TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT,
            name TEXT,
            url TEXT,
            type TEXT,
            FOREIGN KEY(paper_id) REFERENCES papers(id)
        )
    ''')
    
    try:
        c.execute('ALTER TABLE papers ADD COLUMN annotations TEXT')
    except sqlite3.OperationalError:
        pass
        
    c.execute('CREATE INDEX IF NOT EXISTS idx_title ON papers(title);')
    
    c.execute("SELECT COUNT(*) FROM papers")
    if c.fetchone()[0] == 0:
        if os.path.exists('papers.json'):
            with open('papers.json', 'r') as f:
                papers = json.load(f)
                for p in papers:
                    c.execute('''
                        INSERT INTO papers (id, title, authors, year, journal, abstract, pdfUrl)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (p.get('id'), p.get('title'), p.get('authors'), p.get('year'), p.get('journal'), p.get('abstract'), p.get('pdfUrl')))
                    
    conn.commit()
    conn.close()


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, '.'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/')
def index():
    response = send_from_directory('.', 'index.html')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/<path:path>')
def static_files(path):
    response = send_from_directory('.', path)
    if path.endswith(('.html', '.js', '.css')):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/pdfs/<path:filename>')
def serve_pdf(filename):
    return send_from_directory(PDF_DIR, filename)

@app.route('/api/proxy')
def proxy():
    url = request.args.get('url')
    if not url:
        return "No URL provided", 400
    import requests
    from flask import Response
    try:
        r = requests.get(url)
        return Response(r.content, mimetype='application/pdf')
    except Exception as e:
        return str(e), 500

@app.route('/api/papers', methods=['GET'])
def get_papers():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    query = request.args.get('q', '').strip()
    
    if query:
        c.execute("""
            SELECT * FROM papers 
            WHERE title LIKE ? OR authors LIKE ? OR journal LIKE ?
            ORDER BY year DESC
        """, (f'%{query}%', f'%{query}%', f'%{query}%'))
    else:
        c.execute("SELECT * FROM papers ORDER BY year DESC")
        
    papers = [dict(row) for row in c.fetchall()]
    
    # attach attachments
    for p in papers:
        c.execute("SELECT * FROM attachments WHERE paper_id = ?", (p['id'],))
        p['attachments'] = [dict(row) for row in c.fetchall()]
        
    conn.close()
    return jsonify(papers)

@app.route('/api/annotations', methods=['GET'])
def get_annotations():
    paper_id = request.args.get('paper_id')
    if not paper_id:
        return jsonify({"error": "Missing paper_id"}), 400
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT annotations FROM papers WHERE id = ?", (paper_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return jsonify({"annotations": json.loads(row[0])})
    return jsonify({"annotations": {}})

@app.route('/api/annotations', methods=['POST'])
def save_annotations():
    import re
    data = request.json
    paper_id = data.get('paper_id')
    annotations = data.get('annotations')
    if not paper_id:
        return jsonify({"error": "Missing paper_id"}), 400
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE papers SET annotations = ? WHERE id = ?", (json.dumps(annotations), paper_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/rename_attachment', methods=['POST'])
def rename_attachment():
    import re
    data = request.json
    att_id = data.get('id')
    new_name = data.get('new_name')
    if not att_id or not new_name:
        return jsonify({"error": "Missing id or new_name"}), 400
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE attachments SET name = ? WHERE id = ?", (new_name, att_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/delete_attachment', methods=['POST'])
def delete_attachment():
    import re
    data = request.json
    att_id = data.get('id')
    if not att_id:
        return jsonify({"error": "Missing id"}), 400
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT url FROM attachments WHERE id = ?", (att_id,))
    row = c.fetchone()
    if row:
        filepath = os.path.join(PDF_DIR, row[0])
        if os.path.exists(filepath):
            os.remove(filepath)
    c.execute("DELETE FROM attachments WHERE id = ?", (att_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/papers/new_full', methods=['POST'])
def new_paper_full():
    import re
    data = request.json
    import uuid
    new_id = uuid.uuid4().hex
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO papers (id, title, authors, year, journal, abstract, doi, item_type, volume, issue, pages, publisher, isbn, url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (new_id, data.get('title', 'New Item'), data.get('authors', ''), data.get('year', None), data.get('journal', ''), data.get('abstract', ''), data.get('doi', ''), data.get('item_type', 'journalArticle'), data.get('volume', ''), data.get('issue', ''), data.get('pages', ''), data.get('publisher', ''), data.get('isbn', ''), data.get('url', '')))
    conn.commit()
    conn.close()
    return jsonify({"id": new_id, "status": "success"})

@app.route('/api/papers/new', methods=['POST'])
def new_paper():
    import uuid
    new_id = uuid.uuid4().hex
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO papers (id, title, authors, year, journal, abstract)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (new_id, 'New Item', '', None, '', ''))
    conn.commit()
    conn.close()
    return jsonify({"id": new_id, "status": "success"})

@app.route('/api/papers/delete', methods=['POST'])
def delete_paper():
    import re
    data = request.json
    paper_id = data.get('id')
    if not paper_id:
        return jsonify({"error": "Missing id"}), 400
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Delete physical attachment files
    c.execute("SELECT url FROM attachments WHERE paper_id = ?", (paper_id,))
    for row in c.fetchall():
        filepath = os.path.join(PDF_DIR, row[0])
        if os.path.exists(filepath):
            os.remove(filepath)
            
    c.execute("DELETE FROM attachments WHERE paper_id = ?", (paper_id,))
    c.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


@app.route('/api/papers/update_full', methods=['POST'])
def update_paper_full():
    import re
    data = request.json
    paper_id = data.get('id')
    if not paper_id:
        return jsonify({"error": "Missing id"}), 400
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    if 'title' in data: c.execute("UPDATE papers SET title = ? WHERE id = ?", (data['title'], paper_id))
    if 'authors' in data: c.execute("UPDATE papers SET authors = ? WHERE id = ?", (data['authors'], paper_id))
    if 'year' in data: c.execute("UPDATE papers SET year = ? WHERE id = ?", (data['year'], paper_id))
    if 'journal' in data: c.execute("UPDATE papers SET journal = ? WHERE id = ?", (data['journal'], paper_id))
    if 'abstract' in data: c.execute("UPDATE papers SET abstract = ? WHERE id = ?", (data['abstract'], paper_id))
    if 'doi' in data: c.execute("UPDATE papers SET doi = ? WHERE id = ?", (data['doi'], paper_id))
    if 'item_type' in data: c.execute("UPDATE papers SET item_type = ? WHERE id = ?", (data['item_type'], paper_id))
    if 'volume' in data: c.execute("UPDATE papers SET volume = ? WHERE id = ?", (data['volume'], paper_id))
    if 'issue' in data: c.execute("UPDATE papers SET issue = ? WHERE id = ?", (data['issue'], paper_id))
    if 'pages' in data: c.execute("UPDATE papers SET pages = ? WHERE id = ?", (data['pages'], paper_id))
    if 'publisher' in data: c.execute("UPDATE papers SET publisher = ? WHERE id = ?", (data['publisher'], paper_id))
    if 'isbn' in data: c.execute("UPDATE papers SET isbn = ? WHERE id = ?", (data['isbn'], paper_id))
    if 'url' in data: c.execute("UPDATE papers SET url = ? WHERE id = ?", (data['url'], paper_id))
    if "citation_key" in data: c.execute("UPDATE papers SET citation_key = ? WHERE id = ?", (data["citation_key"], paper_id))
    if "pmid" in data: c.execute("UPDATE papers SET pmid = ? WHERE id = ?", (data["pmid"], paper_id))
    if "pmcid" in data: c.execute("UPDATE papers SET pmcid = ? WHERE id = ?", (data["pmcid"], paper_id))
    if "issn" in data: c.execute("UPDATE papers SET issn = ? WHERE id = ?", (data["issn"], paper_id))
    if "archive" in data: c.execute("UPDATE papers SET archive = ? WHERE id = ?", (data["archive"], paper_id))
    if "archive_id" in data: c.execute("UPDATE papers SET archive_id = ? WHERE id = ?", (data["archive_id"], paper_id))
    if "short_title" in data: c.execute("UPDATE papers SET short_title = ? WHERE id = ?", (data["short_title"], paper_id))
    if "language" in data: c.execute("UPDATE papers SET language = ? WHERE id = ?", (data["language"], paper_id))
    if "library_catalog" in data: c.execute("UPDATE papers SET library_catalog = ? WHERE id = ?", (data["library_catalog"], paper_id))
    if "call_number" in data: c.execute("UPDATE papers SET call_number = ? WHERE id = ?", (data["call_number"], paper_id))
    if "license" in data: c.execute("UPDATE papers SET license = ? WHERE id = ?", (data["license"], paper_id))
    if "extra" in data: c.execute("UPDATE papers SET extra = ? WHERE id = ?", (data["extra"], paper_id))
    if "series" in data: c.execute("UPDATE papers SET series = ? WHERE id = ?", (data["series"], paper_id))
    if "edition" in data: c.execute("UPDATE papers SET edition = ? WHERE id = ?", (data["edition"], paper_id))
    if "place" in data: c.execute("UPDATE papers SET place = ? WHERE id = ?", (data["place"], paper_id))

    
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/papers/find_pdf', methods=['POST'])
def find_pdf():
    import requests
    import urllib.parse
    import xml.etree.ElementTree as ET
    import uuid
    import os
    import re
    import tempfile
    
    data = request.json
    paper_id = data.get('id')
    title = data.get('title')
    doi = data.get('doi')
    
    if not paper_id or (not title and not doi):
        return jsonify({"error": "Missing id, title, or doi"}), 400
        
    if doi:
        doi = re.sub(r'^https?://doi\.org/', '', doi, flags=re.IGNORECASE)
        doi = re.sub(r'^doi:', '', doi, flags=re.IGNORECASE).strip()
        
    candidates = []
    
    # Candidate 1: Unpaywall (DOI)
    if doi:
        try:
            r = requests.get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email=lorfioroni@example.com", timeout=10)
            if r.status_code == 200:
                best = r.json()
                if best.get('is_oa') and best.get('best_oa_location'):
                    candidates.append(best['best_oa_location'].get('url_for_pdf'))
        except: pass
        
    # Candidate 2: Unpaywall (Title)
    if title:
        try:
            r = requests.get(f"https://api.unpaywall.org/v2/search?query={urllib.parse.quote(title)}&email=lorfioroni@example.com", timeout=10)
            if r.status_code == 200:
                results = r.json().get('results', [])
                for res in results[:2]:
                    best = res.get('response', {})
                    if best.get('is_oa') and best.get('best_oa_location'):
                        candidates.append(best['best_oa_location'].get('url_for_pdf'))
        except: pass

    # Candidate 3: Google Scholar
    if title:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'}
            gs_url = f"https://scholar.google.com/scholar?q=%22{urllib.parse.quote(title)}%22"
            r = requests.get(gs_url, headers=headers, timeout=10)
            if r.status_code == 200:
                result_match = re.search(r'<div class="gs_r gs_or gs_scl".*?(?=<div class="gs_r gs_or gs_scl"|id="gs_bdy_ft"|$)', r.text, re.DOTALL)
                if result_match:
                    first_res = result_match.group(0)
                    pdf_match = re.search(r'<div class="gs_or_ggsm"[^>]*>.*?<a[^>]+href="([^"]+)"', first_res, re.DOTALL)
                    if pdf_match:
                        pdf_url = pdf_match.group(1)
                        if pdf_url.startswith('/'):
                            pdf_url = "https://scholar.google.com" + pdf_url
                        candidates.append(pdf_url)
        except: pass

    # Candidate 4: arXiv
    if title:
        try:
            r = requests.get(f"http://export.arxiv.org/api/query?search_query=ti:%22{urllib.parse.quote(title)}%22&max_results=1", timeout=10)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                for entry in root.findall('{http://www.w3.org/2005/Atom}entry'):
                    for link in entry.findall('{http://www.w3.org/2005/Atom}link'):
                        if link.attrib.get('title') == 'pdf':
                            candidates.append(link.attrib.get('href'))
                            break
        except: pass

    candidates = [c for c in candidates if c]
    if not candidates:
        return jsonify({"status": "not_found", "message": "Could not find any candidates online."})
        
    def verify_pdf(filepath, title, doi):
        try:
            import pypdf
            from rapidfuzz import fuzz
            reader = pypdf.PdfReader(filepath)
            text = ""
            for i in range(min(2, len(reader.pages))):
                text += reader.pages[i].extract_text() or ""
            
            text_lower = text.lower()
            text_canon = re.sub(r'\s+', '', text_lower)
            
            if doi and re.sub(r'[^a-z0-9]', '', doi.lower()) in re.sub(r'[^a-z0-9]', '', text_lower):
                return True
                
            if title:
                title_canon = re.sub(r'\s+', '', title.lower())
                score = fuzz.partial_ratio(title_canon[:150], text_canon)
                if score >= 85:
                    return True
        except Exception as e:
            print("Verify error:", e)
        return False

    for url in candidates:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200 and 'application/pdf' in r.headers.get('Content-Type', 'application/pdf'):
                fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
                os.write(fd, r.content)
                os.close(fd)
                
                is_valid = verify_pdf(tmp_path, title, doi)
                
                if is_valid:
                    filename = f"{uuid.uuid4().hex}_downloaded.pdf"
                    filepath = os.path.join(PDF_DIR, filename)
                    os.rename(tmp_path, filepath)
                    
                    conn = sqlite3.connect(DB_FILE)
                    c = conn.cursor()
                    c.execute('''
                        INSERT INTO attachments (paper_id, name, url, type)
                        VALUES (?, ?, ?, ?)
                    ''', (paper_id, 'Downloaded PDF', filename, 'pdf'))
                    conn.commit()
                    conn.close()
                    
                    return jsonify({"status": "success"})
                else:
                    os.remove(tmp_path)
        except Exception as e:
            print("Download error:", e)
            
    return jsonify({"status": "not_found", "message": "Candidates were found, but none passed the verification step (wrong paper or login page)."})

@app.route('/api/papers/update', methods=['POST'])

def update_paper():
    import re
    data = request.json
    paper_id = data.get('id')
    field = data.get('field')
    value = data.get('value')
    if not paper_id or not field:
        return jsonify({"error": "Missing id or field"}), 400
    
    valid_fields = ['title', 'authors', 'year', 'journal', 'abstract', 'doi', 'item_type', 'volume', 'issue', 'pages', 'publisher', 'isbn', 'url']
    if field not in valid_fields:
        return jsonify({"error": "Invalid field"}), 400
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"UPDATE papers SET {field} = ? WHERE id = ?", (value, paper_id))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

@app.route('/api/upload_attachment', methods=['POST'])
def upload_attachment():
    paper_id = request.form.get('paper_id')
    if 'file' not in request.files or not paper_id:
        return jsonify({"error": "Missing file or paper_id"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    import uuid
    from werkzeug.utils import secure_filename
    safe_name = secure_filename(file.filename)
    filename = f"{uuid.uuid4().hex}_{safe_name}"
    filepath = os.path.join(PDF_DIR, filename)
    file.save(filepath)
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO attachments (paper_id, name, url, type)
        VALUES (?, ?, ?, ?)
    ''', (paper_id, file.filename, filename, 'pdf'))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success"})

if __name__ == '__main__':
    init_db()
    print("Starting Zotero Clone API on http://localhost:8000")
    app.run(port=8000, debug=True)
