# LU Monitoring — 17 Lapangan Usaha BPS

MVP Python 3.11+ / Streamlit dengan empat agent:
1. X collector: impor CSV atau X API recent search (butuh akses API).
2. News collector: RSS dan ekstraksi artikel, keyword-filter judul/deskripsi.
3. Gemini classifier: klasifikasi tepat satu dari 17 kategori BPS, secondary tags opsional.
4. Gemini summarizer: ringkasan maksimum ~90 kata dengan rujukan URL dari sumber.

## Menjalankan lokal

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
mkdir -p .streamlit
cp .streamlit/secrets.example.toml .streamlit/secrets.toml
# isi GEMINI_API_KEY
streamlit run app.py
```

Di Windows salin file secrets secara manual atau gunakan `copy`.

## Deployment GitHub + Streamlit

Upload seluruh isi folder ke repo GitHub **kecuali** `secrets.toml` dan database sqlite.
Deploy `app.py` melalui Streamlit Community Cloud. Paste secrets di Advanced settings. SQLite lokal di Community Cloud **bukan penyimpanan permanen**: restart/redeploy dapat menghapus hasil. Untuk produksi, ganti `Store` dengan Postgres/Supabase atau database persisten lain sebelum deployment publik.

## CSV postingan X

Minimal header:

```csv
url,text,published,title
https://x.com/i/web/status/123,"Contoh teks postingan tentang panen",2026-10-08,Panen
```

## Perhatian

- Ketersediaan model dan free-tier Gemini bergantung pada project; pilih model yang tersedia di Google AI Studio.
- X API mungkin memerlukan akses berbayar. Jangan mengandalkan scraping tidak resmi atau mengabaikan kebijakan situs.
- Jangan mengekspos aplikasi yang memiliki akses token/API tanpa pembatasan pengguna dan biaya.
- RSS bisa menolak akses artikel; fallback memakai title + summary sehingga hasil bisa terbatas.
- Belum ada retry/backoff khusus 429, batching, penyimpanan produksi, maupun evaluasi manual kategori.
- Skor sentimen bukan fokus MVP; jumlah pemberitaan tidak mewakili nilai output ekonomi BPS.
