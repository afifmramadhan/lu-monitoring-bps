"""Agent 4B: sintesis lintas berita yang sudah diklasifikasikan."""
from google.genai import types
from categories import CATEGORIES


def build_monitoring_report(client, model, rows, category, max_articles=25):
    """Return report and source list; never browse or invent citations."""
    if category not in CATEGORIES:
        raise ValueError('Kode LU tidak dikenal')
    relevant = [r for r in rows if r.get('status') == 'completed' and r.get('primary_lu') == category]
    relevant = relevant[:max_articles]
    if not relevant:
        raise ValueError('Belum ada artikel terklasifikasi untuk kategori ini')

    inputs = []
    sources = []
    for i, r in enumerate(relevant, 1):
        title = str(r.get('title') or '')
        url = str(r.get('url') or '')
        published = str(r.get('published') or '')
        summary = str(r.get('summary') or '')
        # Data yang ada saja; model tidak disuruh membuka tautan.
        inputs.append(f'[{i}] Judul: {title[:280]}\nTanggal: {published[:90]}\nRingkasan: {summary[:1100]}')
        sources.append({'number': i, 'title': title, 'url': url, 'published': published})

    prompt = (
        f'Buat laporan monitoring ekonomi Indonesia untuk Lapangan Usaha BPS {category} '
        f'({CATEGORIES[category]}) dari {len(relevant)} pemberitaan di bawah. '
        'Bahasa Indonesia, singkat dan berbasis bukti. Buat bagian: '
        'Gambaran umum; Perkembangan utama (2-5 poin); Wilayah/pelaku dan angka yang disebut; '
        'Hal yang perlu dipantau; Keterbatasan data. '
        'Setiap pernyataan faktual spesifik wajib diberi penanda rujukan [1], [2], dst sesuai data. '
        'Jangan menyimpulkan pertumbuhan PDB/PDRB hanya berdasarkan jumlah berita; '
        'jangan menyatakan tren jika datanya tidak mendukung; jangan menciptakan angka/wilayah. '
        'Jika sumber tampak duplikat, jangan hitung sebagai peristiwa berbeda. '
        'Berikut ringkasan yang sebelumnya dibuat dari artikel, bukan isi lengkap artikel. '
        'Jika informasinya tidak cukup, nyatakan keterbatasannya.\n\n'
        + '\n\n'.join(inputs)
    )
    result = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=1400),
    )
    report = (result.text or '').strip()
    if not report:
        raise ValueError('Gemini mengembalikan ringkasan kosong')
    return report, sources
