"""News RSS collection, structured Gemini analysis, and safe exports."""
import csv
import math
import hashlib
import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from regions import date_window
from xml.sax.saxutils import escape as xml_escape
from regions import region_for_province

import feedparser
import requests
import trafilatura

HEADERS = ['No', 'Intisari Berita', 'Komponen/LU Terdampak', 'Dampak Sentimen (%)']
CATEGORIES = {
 'A': '01_Pertanian',
 'B': '02_Pertambangan',
 'C': '03_Industri_Pengolahan',
 'D': '04_Listrik_Gas',
 'E': '05_Penyediaan_Air',
 'F': '06_Konstruksi',
 'G': '07_Perdagangan',
 'H': '08_Transportasi',
 'I': '09_Akmamin',
 'J': '10_Informasi_Komunikasi',
 'K': '11_Jasa_Keuangan',
 'L': '12_Real_Estate',
 'MN': '13_Jasa_Perusahaan',
 'O': '14_Adm_Pemerintahan',
 'P': '15_Jasa_Pendidikan',
 'Q': '16_Jasa_Kesehatan',
 'RSTU': '17_Jasa_Lainnya',
}

# Disagregasi inflasi: NONE is internal and must never be forced into a class.
INFLATION = {'1_Core': 'Inflasi Inti', '2_VF': 'Volatile Food',
             '3_AP': 'Administered Prices'}
INFLATION_HEADERS = ['No', 'Intisari Berita', 'Klasifikasi Inflasi', 'Dampak Sentimen (%)']


def normalize_lu(value):
    """Accept BPS letters, numeric codes, and standardized labels from the LLM."""
    raw = str(value or '').strip().upper().replace(' ', '').replace('-', '_')
    normalized = re.sub(r'[^A-Z0-9]', '', raw)
    if normalized in {'NONE', 'TIDAKDAPATDITENTUKAN', 'TIDAKRELEVAN', ''}:
        return 'NONE'
    for code, label in CATEGORIES.items():
        if normalized in {code, re.sub(r'[^A-Z0-9]', '', label.upper())}:
            return code
    match = re.match(r'^(0?[1-9]|1[0-7])(?:_|$|[A-Z])', raw)
    if match:
        return list(CATEGORIES)[int(match.group(1)) - 1]
    raise ValueError(f'Kode LU tidak dikenal: {value}')


def normalize_inflation(value):
    raw = re.sub(r'[^A-Z0-9]', '', str(value or '').upper())
    if raw in {'', 'NONE', 'TIDAKTERKAITINFLASI', 'TIDAKTERKAIT', 'NONINFLASI'}:
        return 'NONE'
    aliases = {
        '1': '1_Core', '1CORE': '1_Core', 'CORE': '1_Core', 'INTI': '1_Core',
        '2': '2_VF', '2VF': '2_VF', 'VF': '2_VF', 'VOLATILEFOOD': '2_VF',
        '3': '3_AP', '3AP': '3_AP', 'AP': '3_AP', 'ADMINISTEREDPRICES': '3_AP'
    }
    if raw not in aliases:
        raise ValueError(f'Klasifikasi inflasi tidak dikenal: {value}')
    return aliases[raw]

MAX_BATCH = 5
MAX_CALLS = 2
MAX_ARTICLES_PER_CLICK = MAX_BATCH * MAX_CALLS

def normalize_keywords(raw):
    return [s.strip().lower() for s in raw.split(',') if s.strip()]

def published_in_window(entry, year, month):
    """Return True only for RSS items whose publication date is in requested window."""
    if year is None: return True
    try:
        parsed = entry.get('published_parsed') or entry.get('updated_parsed')
        if parsed:
            published = datetime(parsed.tm_year, parsed.tm_mon, parsed.tm_mday).date()
        else:
            published = parsedate_to_datetime(entry.get('published') or entry.get('updated')).date()
        start, end = date_window(year, month)
        return start <= published < end
    except (ValueError, TypeError, AttributeError, OverflowError):
        return False

def collect_rss(urls, keywords, max_items=100, fetch_full_text=False, provinces_by_url=None, year=None, month=0):
    """No LLM calls. Collect visible feed text and optionally full article text."""
    seen, records, errors = set(), [], []
    urls = [u for u in urls if u.strip()]
    per_feed_limit = max(1, math.ceil(max_items / max(len(urls), 1)))
    for rss_url in urls:
        feed_count = 0
        rss_url = rss_url.strip()
        if not rss_url:
            continue
        if not rss_url.startswith(('https://', 'http://')):
            errors.append(f'URL feed tidak valid: {rss_url}')
            continue
        target_province = (provinces_by_url or {}).get(rss_url, '')
        try:
            feed = feedparser.parse(rss_url, request_headers={'User-Agent':'LU-Monitoring/1.0'})
            if feed.bozo and not feed.entries:
                errors.append(f'Feed gagal dibaca: {rss_url}')
            for entry in feed.entries:
                if not published_in_window(entry, year, month):
                    continue
                url = entry.get('link', '')
                title = re.sub('<[^>]+>', ' ', entry.get('title', '')).strip()
                excerpt = re.sub('<[^>]+>', ' ', entry.get('summary', '')).strip()
                if not url or not title:
                    continue
                if keywords and not any(k in (title + ' ' + excerpt).lower() for k in keywords):
                    continue
                # Dedupe by normalized URL; URLs are preserved as source evidence.
                fingerprint = hashlib.sha256(url.strip().encode('utf-8')).hexdigest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                body = excerpt
                if fetch_full_text:
                    try:
                        resp = requests.get(url, timeout=7, headers={'User-Agent':'Mozilla/5.0'})
                        resp.raise_for_status()
                        extracted = trafilatura.extract(resp.text)
                        if extracted:
                            body = extracted
                    except requests.RequestException:
                        pass
                records.append({'id': fingerprint, 'title': title, 'url': url,
                                'published': entry.get('published', ''),
                                'bulan': (entry.get('published_parsed').tm_mon if entry.get('published_parsed') else (int(month) or '')),
                                'tahun': (entry.get('published_parsed').tm_year if entry.get('published_parsed') else (int(year) if year else '')),
                                'provinsi_pencarian': target_province or 'Seluruh Indonesia',
                                'pulau_pencarian': region_for_province(target_province) if target_province else 'Seluruh Indonesia',
                                'text': (body or title)[:4500]})
                feed_count += 1
                if len(records) >= max_items:
                    return records, errors
                if feed_count >= per_feed_limit:
                    break
        except Exception as exc:
            errors.append(f'Gagal membaca feed {rss_url}: {type(exc).__name__}')
    return records, errors

def build_prompt(batch):
    inputs = [{'id': a['id'][:16], 'judul': a['title'][:250], 'teks': a['text'][:3000],
               'tanggal': a.get('published', '')} for a in batch]
    return (
        'Analisis setiap berita dalam bahasa Indonesia berdasarkan isi yang tersedia. '
        'Buat intisari maksimum 35 kata; jangan menambahkan fakta. '
        'Pilih SATU kode LU BPS berupa HURUF dari daftar ' + json.dumps(CATEGORIES, ensure_ascii=False) + '. '
        'Jika tidak cukup bukti LU gunakan NONE. '
        'Pilih SATU klasifikasi inflasi: 1_Core untuk harga komoditas/jasa yang termasuk inflasi inti, '
        '2_VF untuk komoditas pangan bergejolak (contoh cabai, bawang, beras), '
        '3_AP untuk harga yang diatur pemerintah (misalnya tarif listrik atau BBM yang diatur). '
        'Jika berita tidak secara jelas membahas pembentukan/perubahan harga atau inflasi, gunakan NONE. '
        'Klasifikasi ini tentang kelompok harga, bukan sektor LU, dan jangan memaksakan klasifikasi. '
        'Skor sentimen NARASI BERITA dari -100 hingga +100 (bilangan bulat); bukan dampak inflasi/PDRB terukur. '
        '0 netral; 1-20 sangat lemah; 21-40 lemah; 41-60 sedang; 61-80 kuat; 81-100 sangat kuat. '
        'Gunakan tanda negatif untuk berita yang merugikan. Nilai berdasarkan realisasi, kepastian dan bukti, '
        'bukan angka kelipatan 5 otomatis. Nilai kecil bila teks minim bukti. '
        'Tulis alasan singkat sentimen dan alasan klasifikasi inflasi. '
        'Jika satu teks membahas banyak kategori, pilih yang paling jelas dominan. '
        'Jawab JSON valid (tanpa markdown) tepat dengan bentuk '
        '{"items":[{"id":"...","intisari":"...","lu":"A","inflasi":"NONE",'
        '"sentimen":0,"alasan_sentimen":"...","alasan_inflasi":"..."}]}. '
        'Gunakan ID yang sama dengan input dan jangan menghilangkan item. DATA:\n'
        + json.dumps(inputs, ensure_ascii=False)
    )


def validate_response_partial(response_text, batch):
    """Return valid items and per-item errors; a bad category must not discard a batch."""
    data = json.loads(response_text)
    items = data.get('items')
    if not isinstance(items, list):
        raise ValueError('items bukan list')
    by_id = {str(i.get('id')): i for i in items if isinstance(i, dict)}
    validated, failures = [], []
    for article in batch:
        key = article['id'][:16]
        try:
            if key not in by_id:
                raise ValueError('ID artikel tidak dikembalikan Gemini')
            item = by_id[key]
            lu = normalize_lu(item.get('lu'))
            inflation = normalize_inflation(item.get('inflasi', 'NONE'))
            score = item.get('sentimen')
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not -100 <= score <= 100:
                raise ValueError('Skor sentimen tidak valid')
            summary = str(item.get('intisari', '')).strip()
            if not summary:
                raise ValueError('Intisari kosong')
            validated.append({**article, 'intisari': summary, 'lu': lu,
                              'inflasi': inflation, 'sentimen': int(round(score)),
                              'alasan_sentimen': str(item.get('alasan_sentimen', ''))[:300],
                              'alasan_inflasi': str(item.get('alasan_inflasi', ''))[:300]})
        except (ValueError, TypeError) as exc:
            failures.append({'id': key, 'message': str(exc)})
    return validated, failures


def validate_response(response_text, batch):
    """Compatibility wrapper; accepts valid items even if one item is malformed."""
    return validate_response_partial(response_text, batch)[0]

def analyze_batches(pending, api_key, model, max_calls=2):
    """No retries. One request per batch; stop after max_calls, including failures."""
    from google import genai
    client = genai.Client(api_key=api_key)
    outputs, failures = [], []
    calls = 0
    for offset in range(0, min(len(pending), MAX_ARTICLES_PER_CLICK), MAX_BATCH):
        if calls >= min(max_calls, MAX_CALLS):
            break
        batch = pending[offset:offset + MAX_BATCH]
        calls += 1  # count BEFORE invoking API, including 429s
        try:
            response = client.models.generate_content(
                model=model,
                contents=build_prompt(batch),
                config={'response_mime_type':'application/json', 'temperature':0.2},
            )
            valid, invalid = validate_response_partial(response.text, batch)
            outputs.extend(valid)
            for issue in invalid:
                failures.append({'batch': calls, 'message': issue['id'] + ': ' + issue['message']})
        except Exception as exc:
            msg = str(exc)
            failures.append({'batch':calls, 'message': msg[:400]})
            if '429' in msg or 'RESOURCE_EXHAUSTED' in msg:
                break  # don't spend the second call if rate-limited
    return outputs, failures, calls

def get_rows(analyzed):
    result = []
    for i, article in enumerate(analyzed, 1):
        lu = article['lu']
        result.append({
            'No':i,
            'Intisari Berita':article['intisari'],
            'Komponen/LU Terdampak': ('Tidak dapat ditentukan' if lu == 'NONE' else CATEGORIES[lu]),
            'Dampak Sentimen (%)':f"{article['sentimen']}%",
        })
    return result

def csv_bytes(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=HEADERS)
    writer.writeheader()
    # Prevent CSV formula injection into spreadsheet applications.
    for row in rows:
        writer.writerow({k: safe_cell(row[k]) for k in HEADERS})
    return stream.getvalue().encode('utf-8-sig')

def safe_cell(value):
    text = str(value)
    if re.fullmatch(r'-?\d{1,3}%', text):
        return text
    if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + text
    return text

def xlsx_bytes(rows):
    """Small Excel XLSX generated with OOXML, using only standard library."""
    all_rows = [HEADERS] + [[r[h] for h in HEADERS] for r in rows]
    row_xml = []
    for r_index, values in enumerate(all_rows, 1):
        cells = []
        for c_index, value in enumerate(values):
            ref = chr(ord('A') + c_index) + str(r_index)
            cell_text = xml_escape(safe_cell(value))
            if r_index > 1 and c_index == 3 and re.fullmatch(r'-?\d{1,3}%', str(value)):
                numeric = int(str(value)[:-1]) / 100
                cells.append(f'<c r="{ref}" s="2"><v>{numeric:g}</v></c>')
                continue
            style_id = ' s="1"' if r_index == 1 else ''
            cells.append(f'<c r="{ref}" t="inlineStr"{style_id}><is><t xml:space="preserve">{cell_text}</t></is></c>')
        row_xml.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    last = max(len(all_rows), 1)
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
       f'<dimension ref="A1:D{last}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
       f'<cols><col min="1" max="1" width="9" customWidth="1"/>'
       f'<col min="2" max="2" width="78" customWidth="1"/>'
       f'<col min="3" max="3" width="49" customWidth="1"/>'
       f'<col min="4" max="4" width="26" customWidth="1"/></cols>'
       f'<sheetData>{"".join(row_xml)}</sheetData><autoFilter ref="A1:D{last}"/></worksheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
       '<sheets><sheet name="Sentimen" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
       '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
       '</Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
       '<fonts count="2"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts>'
       '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF17365D"/><bgColor indexed="64"/></patternFill></fill></fills>'
       '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
       '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
       '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
       '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
       '<xf numFmtId="9" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
       '</styleSheet>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
       '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
       '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
       '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
       '</Relationships>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in {
            '[Content_Types].xml':content_types, '_rels/.rels':root_rels,
            'xl/workbook.xml':workbook, 'xl/_rels/workbook.xml.rels':rels,
            'xl/worksheets/sheet1.xml':sheet, 'xl/styles.xml':styles,
        }.items():
            z.writestr(name, content)
    return out.getvalue()


def get_detail_rows(ordered):
    """Audit export: region denotes *search target*, not verified event location."""
    rows = []
    for i, article in enumerate(ordered, 1):
        rows.append({
            'No': i, 'Pulau/Kawasan Pencarian': article.get('pulau_pencarian', 'Seluruh Indonesia'),
            'Provinsi Pencarian': article.get('provinsi_pencarian', 'Seluruh Indonesia'),
            'Judul': article['title'], 'Tanggal Publikasi': article.get('published', ''),
            'URL Sumber': article['url'], 'LU BPS': CATEGORIES.get(article.get('lu'), 'Tidak dapat ditentukan'),
            'Bulan': article.get('bulan', ''), 'Tahun': article.get('tahun', ''),
            'Skor Sentimen': article.get('sentimen', 0), 'Alasan Sentimen': article.get('alasan_sentimen', ''),
            'Klasifikasi Inflasi': article.get('inflasi', 'NONE'),
            'Alasan Inflasi': article.get('alasan_inflasi', ''),
        })
    return rows

def detail_csv_bytes(rows):
    fields = ['No','Pulau/Kawasan Pencarian','Provinsi Pencarian','Judul','Tanggal Publikasi','URL Sumber','LU BPS','Bulan','Tahun','Skor Sentimen','Alasan Sentimen','Klasifikasi Inflasi','Alasan Inflasi']
    buff = io.StringIO(newline='')
    writer = csv.DictWriter(buff, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k:safe_cell(row[k]) for k in fields})
    return buff.getvalue().encode('utf-8-sig')

def regional_xlsx_bytes(sentimen_rows, detail_rows):
    """Keep sheet Sentimen EXACTLY four columns; append sheet Detail_Wilayah."""
    original = xlsx_bytes(sentimen_rows)
    extra_fields = ['No','Pulau/Kawasan Pencarian','Provinsi Pencarian','Judul','Tanggal Publikasi','URL Sumber','LU BPS','Bulan','Tahun','Skor Sentimen','Alasan Sentimen','Klasifikasi Inflasi','Alasan Inflasi']
    def col_name(number):
        label = ''
        while number:
            number, remainder = divmod(number - 1, 26)
            label = chr(65 + remainder) + label
        return label
    xml_rows = []
    for row_index, vals in enumerate([extra_fields] + [[r.get(f, '') for f in extra_fields] for r in detail_rows], 1):
        cells = []
        for column_index, val in enumerate(vals, 1):
            safe = xml_escape(safe_cell(val))
            head = ' s="1"' if row_index == 1 else ''
            cells.append(f'<c r="{col_name(column_index)}{row_index}" t="inlineStr"{head}><is><t xml:space="preserve">{safe}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    last = max(len(detail_rows)+1,1)
    detail_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:M{last}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<cols><col min="1" max="1" width="8" customWidth="1"/>'
        '<col min="2" max="3" width="28" customWidth="1"/>'
        '<col min="4" max="4" width="65" customWidth="1"/>'
        '<col min="5" max="5" width="28" customWidth="1"/>'
        '<col min="6" max="6" width="70" customWidth="1"/>'
        '<col min="7" max="9" width="20" customWidth="1"/><col min="10" max="10" width="50" customWidth="1"/></cols>'
        f'<sheetData>{"".join(xml_rows)}</sheetData><autoFilter ref="A1:M{last}"/></worksheet>')
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original),'r') as source, zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            content = source.read(name).decode('utf-8')
            if name == 'xl/workbook.xml':
                content = content.replace('</sheets>', '<sheet name="Detail_Wilayah" sheetId="2" r:id="rId3"/></sheets>')
            elif name == 'xl/_rels/workbook.xml.rels':
                content = content.replace('</Relationships>', '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
            elif name == '[Content_Types].xml':
                content = content.replace('</Types>', '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            target.writestr(name, content)
        target.writestr('xl/worksheets/sheet2.xml', detail_xml)
    return output.getvalue()


def get_inflation_rows(ordered):
    """Only inflation-related articles enter sheet Inflasi; NONE stays in audit."""
    relevant = [a for a in ordered if a.get('inflasi') in INFLATION]
    return [{'No': i, 'Intisari Berita': a['intisari'],
             'Klasifikasi Inflasi': a['inflasi'],
             'Dampak Sentimen (%)': f"{a['sentimen']}%"}
            for i, a in enumerate(relevant, 1)]


def inflation_csv_bytes(rows):
    buff = io.StringIO(newline='')
    writer = csv.DictWriter(buff, fieldnames=INFLATION_HEADERS)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_cell(row[k]) for k in INFLATION_HEADERS})
    return buff.getvalue().encode('utf-8-sig')


def regional_inflation_xlsx_bytes(sentimen_rows, detail_rows, inflation_rows):
    """Append the Inflasi sheet while preserving existing Sentimen and Detail_Wilayah."""
    original = regional_xlsx_bytes(sentimen_rows, detail_rows)
    sheet_rows = [INFLATION_HEADERS] + [[r[h] for h in INFLATION_HEADERS] for r in inflation_rows]
    rendered = []
    for idx, row in enumerate(sheet_rows, 1):
        cells = []
        for col, value in enumerate(row):
            ref = f'{chr(65+col)}{idx}'
            if idx > 1 and col == 3:
                number = int(str(value).removesuffix('%')) / 100
                cells.append(f'<c r="{ref}" s="2"><v>{number:g}</v></c>')
            else:
                header = ' s="1"' if idx == 1 else ''
                cells.append(f'<c r="{ref}" t="inlineStr"{header}><is><t>{xml_escape(safe_cell(value))}</t></is></c>')
        rendered.append(f'<row r="{idx}">{"".join(cells)}</row>')
    last = len(sheet_rows)
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<dimension ref="A1:D{last}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
             '<cols><col min="1" max="1" width="8" customWidth="1"/>'
             '<col min="2" max="2" width="76" customWidth="1"/>'
             '<col min="3" max="3" width="28" customWidth="1"/>'
             '<col min="4" max="4" width="25" customWidth="1"/></cols>'
             f'<sheetData>{"".join(rendered)}</sheetData><autoFilter ref="A1:D{last}"/></worksheet>')
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as zin, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
        for filename in zin.namelist():
            content = zin.read(filename)
            if filename in {'xl/workbook.xml', 'xl/_rels/workbook.xml.rels', '[Content_Types].xml'}:
                text = content.decode('utf-8')
                if filename == 'xl/workbook.xml':
                    text = text.replace('</sheets>', '<sheet name="Inflasi" sheetId="3" r:id="rId4"/></sheets>')
                elif filename == 'xl/_rels/workbook.xml.rels':
                    text = text.replace('</Relationships>', '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/></Relationships>')
                else:
                    text = text.replace('</Types>', '<Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
                content = text.encode('utf-8')
            zout.writestr(filename, content)
        zout.writestr('xl/worksheets/sheet3.xml', sheet)
    return out.getvalue()
