import os
import pandas as pd
import streamlit as st
from agents.news import collect_news
from agents.x_collector import collect_x, import_x_csv
from categories import CATEGORIES
from pipeline import process
from storage import Store

st.set_page_config(page_title='LU Monitoring BPS',layout='wide')
st.title('Monitoring Ekonomi — 17 Lapangan Usaha BPS')
st.caption('MVP: pengumpulan manual, klasifikasi AI, ringkasan bersumber. Jumlah berita bukan ukuran resmi kondisi PDB/PDRB.')

def secret(key, default=''):
    try:
        return st.secrets.get(key,os.getenv(key,default))
    except FileNotFoundError:
        return os.getenv(key,default)

# SQLite persisten di komputer lokal. Pada Streamlit Community Cloud gunakan storage eksternal untuk persistensi.
store = Store()
with st.sidebar:
    st.header('Pengaturan AI')
    model = st.text_input('Model klasifikasi',value=secret('CLASSIFIER_MODEL','gemini-3.5-flash-lite'))
    summary_model = st.text_input('Model ringkasan',value=secret('SUMMARY_MODEL','gemini-3.5-flash-lite'))
    n = st.slider('Batas proses',10,500,100,10)
    st.caption('API key disimpan di Secrets; jangan diketik ke GitHub.')

with st.expander('1. Koleksi data',expanded=True):
    tab_news, tab_x = st.tabs(['Berita RSS','X / Twitter'])
    with tab_news:
        feed_text = st.text_area('URL RSS (satu per baris)',height=100,
                                 placeholder='https://situs-berita.example/rss')
        keywords = st.text_input('Filter judul/deskripsi (pisahkan koma)',value='pertanian, perdagangan, industri, investasi, ekonomi')
        if st.button('Agent 2: Ambil berita'):
            urls = [u.strip() for u in feed_text.splitlines() if u.strip()]
            if not urls:
                st.warning('Masukkan URL RSS terlebih dahulu.')
            else:
                with st.spinner('Mengambil feed dan artikel...'):
                    docs = collect_news(urls,n,keywords)
                    store.add(docs)
                st.success(f'{len(docs)} artikel ditemukan (duplikat URL diabaikan saat disimpan).')
    with tab_x:
        uploaded=st.file_uploader('Alternatif tanpa X API: CSV dengan kolom url,text,published,title',type=['csv'])
        if st.button('Agent 1: Impor CSV X') and uploaded is not None:
            docs=import_x_csv(uploaded)
            store.add(docs[:n])
            st.success(f'{min(n,len(docs))} baris CSV diimpor.')
        x_query=st.text_input('Query X API',value='(pertanian OR industri OR perdagangan) lang:id -is:retweet')
        if st.button('Agent 1: Ambil via X API'):
            token=secret('X_BEARER_TOKEN')
            if not token:
                st.warning('X_BEARER_TOKEN belum disetel.')
            else:
                try:
                    with st.spinner('Menghubungi X API...'):
                        docs=collect_x(x_query,token,n)
                        store.add(docs)
                    st.success(f'{len(docs)} postingan diambil.')
                except Exception as exc:
                    st.error(f'X API gagal: {exc}')

st.header('2. Analisis Agent 3 dan Agent 4')
if st.button('Jalankan klasifikasi & ringkasan',type='primary'):
    key=secret('GEMINI_API_KEY')
    if not key:
        st.error('Setel GEMINI_API_KEY melalui Streamlit Secrets terlebih dahulu.')
    else:
        bar=st.progress(0)
        label=st.empty()
        def on_progress(done,total):
            bar.progress(done/max(total,1))
            label.caption(f'Selesai {done}/{total} dokumen')
        with st.spinner('Memanggil Gemini...'):
            count=process(store,key,model,summary_model,n,on_progress)
        st.success(f'{count} dokumen dicoba. Lihat status error untuk kegagalan.')

st.header('3. Dashboard')
rows=store.rows()
if not rows:
    st.info('Belum ada data. Ambil RSS atau impor CSV X lalu jalankan analisis.')
else:
    df=pd.DataFrame(rows)
    a,b,c=st.columns(3)
    a.metric('Total dokumen',len(df))
    b.metric('Sudah diklasifikasi',int(df.status.isin(['completed','irrelevant']).sum()))
    c.metric('Menunggu / error',int(df.status.isin(['pending','error']).sum()))
    relevant=df[df.status.eq('completed')].copy()
    if not relevant.empty:
        relevant['kategori']=relevant.primary_lu.map(lambda x:f'{x} — {CATEGORIES.get(x,x)}')
        st.subheader('Sebaran berita per kategori utama')
        st.bar_chart(relevant.groupby('kategori').size().reindex([f'{k} — {v}' for k,v in CATEGORIES.items()],fill_value=0))
        st.caption('Jumlah kemunculan berita, bukan laju pertumbuhan ekonomi atau perubahan PDRB.')
    chosen=st.selectbox('Filter kategori', ['Semua']+list(CATEGORIES))
    view=df if chosen=='Semua' else df[df.primary_lu.eq(chosen)]
    st.dataframe(view[['source','published','title','primary_lu','status','summary','url','needs_review','error']],hide_index=True,use_container_width=True,
                 column_config={'url':st.column_config.LinkColumn('Sumber')})
    st.download_button('Unduh CSV',view.to_csv(index=False).encode('utf-8-sig'),file_name='lu_monitoring.csv',mime='text/csv')


st.header('4. Agent 4 — Laporan monitoring gabungan')
st.caption('Sintesis berita yang sudah selesai diklasifikasikan; tidak memanggil scraper lagi.')
report_rows = [r for r in store.rows() if r.get('status') == 'completed' and r.get('primary_lu') in CATEGORIES]
report_counts = {code: sum(r['primary_lu'] == code for r in report_rows) for code in CATEGORIES}
available = [c for c, cnt in report_counts.items() if cnt]
if not available:
    st.info('Belum ada berita relevan yang selesai diklasifikasikan untuk dibuat laporan.')
else:
    report_category = st.selectbox(
        'Kategori LU untuk laporan gabungan', available,
        format_func=lambda c: f'{c} — {CATEGORIES[c]} ({report_counts[c]} berita)',
        key='report_category'
    )
    max_report_articles = st.slider('Maksimum artikel untuk laporan', 1, 50, 20, key='max_report_articles')
    st.caption('Satu klik = satu permintaan AI. Laporan disimpan sementara di sesi browser, bukan database.')
    if st.button('Buat summary monitoring per LU', type='primary'):
        key = secret('GEMINI_API_KEY')
        if not key:
            st.error('GEMINI_API_KEY belum disetel di Streamlit Secrets.')
        else:
            from agents.classifier import make_client
            from agents.monitoring_report import build_monitoring_report
            try:
                with st.spinner('Agent 4 menyusun laporan gabungan...'):
                    report, sources = build_monitoring_report(
                        make_client(key), summary_model, report_rows,
                        report_category, max_report_articles
                    )
                st.session_state['monitoring_report'] = {
                    'category': report_category, 'text': report, 'sources': sources
                }
            except Exception as exc:
                st.error(f'Gagal membuat laporan: {exc}')
    report_result = st.session_state.get('monitoring_report')
    if report_result:
        code = report_result['category']
        st.subheader(f'Laporan: {code} — {CATEGORIES[code]}')
        st.markdown(report_result['text'])
        st.subheader('Sumber berita yang digunakan')
        for item in report_result['sources']:
            if item['url'].startswith(('https://', 'http://')):
                st.markdown(f"[{item['number']}] [{item['title']}]({item['url']}) — {item['published']}")
            else:
                st.write(f"[{item['number']}] {item['title']} — {item['published']}")
        exported = (f"Laporan monitoring LU {code} — {CATEGORIES[code]}\n\n"
                    + report_result['text'] + '\n\nSUMBER\n'
                    + '\n'.join(f"[{r['number']}] {r['title']} | {r['url']} | {r['published']}" for r in report_result['sources']))
        st.download_button('Unduh laporan TXT', exported.encode('utf-8'),
                           file_name=f'laporan_LU_{code}.txt', mime='text/plain')
