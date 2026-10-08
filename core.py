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
from xml.sax.saxutils import escape as xml_escape
from regions import region_for_province

import feedparser
import requests
import trafilatura

HEADERS = ['No', 'Intisari Berita', 'Komponen/LU Terdampak', 'Dampak Sentimen (%)']
CATEGORIES = {
 'A': 'Pertanian, Kehutanan, dan Perikanan',
 'B': 'Pertambangan dan Penggalian',
 'C': 'Industri Pengolahan',
 'D': 'Pengadaan Listrik dan Gas',
 'E': 'Pengadaan Air, Pengelolaan Sampah, Limbah dan Daur Ulang',
 'F': 'Konstruksi',
 'G': 'Perdagangan Besar dan Eceran; Reparasi Mobil dan Sepeda Motor',
 'H': 'Transportasi dan Pergudangan',
 'I': 'Penyediaan Akomodasi dan Makan Minum',
 'J': 'Informasi dan Komunikasi',
 'K': 'Jasa Keuangan dan Asuransi',
 'L': 'Real Estat',
 'MN': 'Jasa Perusahaan',
 'O': 'Administrasi Pemerintahan, Pertahanan dan Jaminan Sosial Wajib',
 'P': 'Jasa Pendidikan',
 'Q': 'Jasa Kesehatan dan Kegiatan Sosial',
 'RSTU': 'Jasa Lainnya',
}
MAX_BATCH = 5
MAX_CALLS = 2
MAX_ARTICLES_PER_CLICK = MAX_BATCH * MAX_CALLS

def normalize_keywords(raw):
    return [s.strip().lower() for s in raw.split(',') if s.strip()]

def collect_rss(urls, keywords, max_items=100, fetch_full_text=False, provinces_by_url=None):
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
        'Tugas: buat intisari dan skor SENTIMEN NARASI BERITA, bukan prediksi perubahan PDB. '
        'Untuk setiap item, kembalikan tepat satu objek dengan id yang sama. Bahasa Indonesia. '
        'Intisari maksimum 35 kata dan hanya berdasarkan isi input. '
        'Pilih SATU kategori utama dari kode 17 LU BPS berikut: '
        + json.dumps(CATEGORIES, ensure_ascii=False) + '. '
        'Jika isi terlalu umum/tidak cukup untuk menentukan LU, gunakan kategori NONE. '
        'Skor sentimen adalah bilangan bulat -100 sampai +100: negatif untuk nada merugikan, '
        'nol netral, positif untuk nada menguntungkan. Ini bukan persentase dampak ekonomi terukur. '
        'Jangan mengarang dampak, tanggal, angka atau fakta. '
        'Jawab JSON valid tanpa markdown dalam struktur {"items":[{"id":"...",'
        '"intisari":"...","lu":"A","sentimen":0}]}. '
        'Data:\n' + json.dumps(inputs, ensure_ascii=False)
    )

def validate_response(response_text, batch):
    data = json.loads(response_text)
    items = data['items']
    if not isinstance(items, list):
        raise ValueError('items bukan list')
    by_id = {}
    for item in items:
        key = str(item['id'])
        if key in by_id:
            raise ValueError('ID ganda dari Gemini')
        by_id[key] = item
    validated = []
    for article in batch:
        key = article['id'][:16]
        if key not in by_id:
            raise ValueError(f'Gemini tidak mengembalikan ID {key}')
        item = by_id[key]
        lu = str(item['lu']).upper().replace(',', '').replace(' ', '')
        if lu not in CATEGORIES and lu != 'NONE':
            raise ValueError(f'Kode LU tidak dikenal: {lu}')
        score = item['sentimen']
        if isinstance(score, bool) or not isinstance(score, (int, float)) or score < -100 or score > 100:
            raise ValueError('Skor sentimen tidak valid')
        summary = str(item['intisari']).strip()
        if not summary:
            raise ValueError('Intisari kosong')
        validated.append({**article, 'intisari':summary,
                          'lu':lu, 'sentimen':int(round(score))})
    return validated

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
            outputs.extend(validate_response(response.text, batch))
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
            'Komponen/LU Terdampak': ('Tidak dapat ditentukan' if lu == 'NONE' else f'{lu} — {CATEGORIES[lu]}'),
            'Dampak Sentimen (%)':f"{article['sentimen']:+d}%" if article['sentimen'] else '0%',
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
       '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
       '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
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
            'URL Sumber': article['url'], 'LU BPS': article.get('lu','NONE'),
            'Skor Sentimen': article.get('sentimen', 0),
        })
    return rows

def detail_csv_bytes(rows):
    fields = ['No','Pulau/Kawasan Pencarian','Provinsi Pencarian','Judul','Tanggal Publikasi','URL Sumber','LU BPS','Skor Sentimen']
    buff = io.StringIO(newline='')
    writer = csv.DictWriter(buff, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k:safe_cell(row[k]) for k in fields})
    return buff.getvalue().encode('utf-8-sig')

def regional_xlsx_bytes(sentimen_rows, detail_rows):
    """Keep sheet Sentimen EXACTLY four columns; append sheet Detail_Wilayah."""
    original = xlsx_bytes(sentimen_rows)
    extra_fields = ['No','Pulau/Kawasan Pencarian','Provinsi Pencarian','Judul','Tanggal Publikasi','URL Sumber','LU BPS','Skor Sentimen']
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
        f'<dimension ref="A1:H{last}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<cols><col min="1" max="1" width="8" customWidth="1"/>'
        '<col min="2" max="3" width="28" customWidth="1"/>'
        '<col min="4" max="4" width="65" customWidth="1"/>'
        '<col min="5" max="5" width="28" customWidth="1"/>'
        '<col min="6" max="6" width="70" customWidth="1"/>'
        '<col min="7" max="8" width="20" customWidth="1"/></cols>'
        f'<sheetData>{"".join(xml_rows)}</sheetData><autoFilter ref="A1:H{last}"/></worksheet>')
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
