import streamlit as st
from core import (MAX_ARTICLES_PER_CLICK, analyze_batches, collect_rss, csv_bytes,
                  detail_csv_bytes, get_detail_rows, get_rows, regional_xlsx_bytes)
from regions import REGIONS, MONTH_NAMES, build_news_rss_urls, selected_provinces

st.set_page_config(page_title='Monitoring Ekonomi Regional — BPS', layout='wide')
st.title('Monitoring Ekonomi Regional — 17 LU BPS')
st.caption('Pilih wilayah SEBELUM mencari RSS. Dua request Gemini maksimal per klik analisis; scraping RSS tanpa AI.')

for key, default in [('articles', []), ('analyzed', {}), ('search_context', '')]:
    if key not in st.session_state:
        st.session_state[key] = default

with st.sidebar:
    st.header('Pengaturan AI')
    model = st.text_input('Model Gemini', value=st.secrets.get('GEMINI_MODEL','gemini-3.5-flash-lite'))
    st.caption('Maksimal 2 panggilan per klik; 5 artikel per panggilan. Tidak ada retry otomatis.')

st.subheader('1. Wilayah pencarian dan scraping RSS')
island = st.selectbox('Pulau / kawasan', ['Seluruh Indonesia'] + list(REGIONS))
province_options = ['Semua provinsi'] if island == 'Seluruh Indonesia' else ['Semua provinsi'] + REGIONS[island]
province = st.selectbox('Provinsi tujuan', province_options)
d1, d2 = st.columns(2)
with d1:
    year = st.selectbox('Tahun publikasi', list(range(2026, 2019, -1)))
with d2:
    month = st.selectbox('Bulan publikasi', list(range(13)), format_func=lambda x: MONTH_NAMES[x])
terms_text = st.text_input('Kata kunci ekonomi (pisahkan dengan koma)',
                          'ekonomi, pertanian, perdagangan, industri, investasi')
keywords = [term.strip() for term in terms_text.split(',') if term.strip()][:10]
try:
    urls = build_news_rss_urls(island, province, keywords, year=year, month=month)
    targets = selected_provinces(island, province)
except ValueError as error:
    st.error(str(error))
    st.stop()

st.info(f'Pencarian aktif: **{island}** / **{province}**. ' +
        f'Periode publikasi: **{MONTH_NAMES[month]} {year}**. Google News RSS akan dicari menggunakan **{len(urls)} query yang menyebut lokasi pilihan**. '
        'Wilayah hasil adalah TARGET PENCARIAN, bukan kepastian lokasi kejadian pada artikel.')
with st.expander('Lihat query RSS yang akan digunakan'):
    for url in urls:
        st.code(url, language=None)

c1, c2 = st.columns(2)
with c1:
    max_feed = st.slider('Maksimum berita dikumpulkan', 10, 500, 100, step=10)
with c2:
    full_text = st.checkbox('Coba ambil isi artikel lengkap (lebih lambat)', value=False)

if st.button('Cari berita di wilayah pilihan', type='primary'):
    with st.spinner('Mencari RSS sesuai wilayah sebelum analisis...'):
        # The generated query already includes keyword constraints; do not
        # reject matches merely because Google News rewrites the title.
        province_map = {url:p for url,p in zip(urls,targets)} if targets else {}
        records, warnings = collect_rss(urls, [], max_items=max_feed,
                                        fetch_full_text=full_text,
                                        provinces_by_url=province_map, year=year, month=month)
    st.session_state.articles = records
    st.session_state.analyzed = {k:v for k,v in st.session_state.analyzed.items()
                                 if k in {a['id'] for a in records}}
    st.session_state.search_context = f'{island} / {province} / {MONTH_NAMES[month]} {year}'
    st.success(f'{len(records)} berita terkumpul dari query wilayah pilihan.')
    for warning in warnings[:5]:
        st.warning(warning)

articles = st.session_state.articles
analyzed = st.session_state.analyzed
pending = [article for article in articles if article['id'] not in analyzed]
if articles:
    st.caption('Dataset aktif dari pencarian: ' + st.session_state.search_context +
               '. Mengubah pilihan wilayah tidak mengubah data yang sudah terkumpul. Klik Cari berita lagi.')
cols = st.columns(3)
cols[0].metric('Berita terkumpul', len(articles))
cols[1].metric('Sudah dianalisis', len([a for a in articles if a['id'] in analyzed]))
cols[2].metric('Menunggu analisis', len(pending))

st.subheader('2. Intisari berita + LU + sentimen (Gemini)')
st.write(f'Setiap klik memproses **maksimal {MAX_ARTICLES_PER_CLICK} artikel** dengan **maksimal 2 request Gemini**. '
         'Berita yang berhasil dianalisis tidak dipanggil ulang selama sesi Streamlit masih aktif.')
if st.button('Analisis maksimal 10 berita berikutnya', disabled=not pending):
    key = st.secrets.get('GEMINI_API_KEY')
    if not key:
        st.error('Atur GEMINI_API_KEY lewat Streamlit App Settings → Secrets.')
    else:
        with st.spinner('Menganalisis berita...'):
            results, errors, calls = analyze_batches(pending, key, model)
        for result in results:
            st.session_state.analyzed[result['id']] = result
        st.info(f'{calls}/2 request dicoba; {len(results)} artikel berhasil dianalisis.')
        for error in errors:
            st.error(f"Batch {error['batch']}: {error['message']}")
        if any('429' in item['message'] for item in errors):
            st.warning('Kuota/rate limit Google tercapai; tidak ada retry otomatis. Coba lagi setelah batas pulih.')

st.subheader('3. Hasil + ekspor CSV / Excel')
ordered = [analyzed[a['id']] for a in articles if a['id'] in analyzed]
rows = get_rows(ordered)
detail_rows = get_detail_rows(ordered)
if rows:
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption('Sheet Sentimen / CSV utama: tepat 4 kolom sesuai template. '
               'Excel juga punya sheet Detail_Wilayah untuk audit bulan, tahun, judul, URL, kategori, dan alasan sentimen.')
    left, mid, right = st.columns(3)
    with left:
        st.download_button('CSV Sentimen (4 kolom)', csv_bytes(rows),
                           'Sentimen.csv', mime='text/csv', use_container_width=True)
    with mid:
        st.download_button('CSV Detail Wilayah', detail_csv_bytes(detail_rows),
                           'Detail_Wilayah.csv', mime='text/csv', use_container_width=True)
    with right:
        st.download_button('Excel 2 sheet', regional_xlsx_bytes(rows, detail_rows),
                           'Sentimen_Regional.xlsx',
                           mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           use_container_width=True)
    with st.expander('Audit artikel dan wilayah pencarian'):
        st.dataframe(detail_rows, hide_index=True, use_container_width=True)
else:
    st.info('Belum ada analisis. Pilih wilayah, cari RSS, lalu jalankan analisis.')
st.caption('Persentase dalam Excel adalah sel numerik terformat, dan CSV tidak memakai petik awalan. Skor sentimen adalah perkiraan nada berita oleh AI (-100% hingga +100%), bukan dampak ekonomi aktual. '
           'Artikel hasil query wilayah bisa saja membahas wilayah lain; periksa URL sumber sebelum mengambil keputusan. '
           'Hasil tersimpan di sesi, bukan database permanen; unduh sebelum restart/redeploy.')
