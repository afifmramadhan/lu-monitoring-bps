"""38 provinsi dan 7 kelompok wilayah untuk pembentukan query RSS sebelum pencarian."""
from urllib.parse import urlencode

REGIONS = {
    'Sumatera': ['Aceh','Sumatera Utara','Sumatera Barat','Riau','Kepulauan Riau','Jambi','Sumatera Selatan','Bengkulu','Lampung','Kepulauan Bangka Belitung'],
    'Jawa': ['DKI Jakarta','Jawa Barat','Jawa Tengah','DI Yogyakarta','Jawa Timur','Banten'],
    'Kalimantan': ['Kalimantan Barat','Kalimantan Tengah','Kalimantan Selatan','Kalimantan Timur','Kalimantan Utara'],
    'Sulawesi': ['Sulawesi Utara','Gorontalo','Sulawesi Tengah','Sulawesi Barat','Sulawesi Selatan','Sulawesi Tenggara'],
    'Bali dan Nusa Tenggara': ['Bali','Nusa Tenggara Barat','Nusa Tenggara Timur'],
    'Maluku': ['Maluku','Maluku Utara'],
    'Papua': ['Papua','Papua Barat','Papua Barat Daya','Papua Tengah','Papua Pegunungan','Papua Selatan'],
}
ALL_PROVINCES = [province for provinces in REGIONS.values() for province in provinces]
DEFAULT_TERMS = ['ekonomi','pertanian','perdagangan','industri','investasi']

def selected_provinces(island='Seluruh Indonesia', province='Semua provinsi'):
    if island == 'Seluruh Indonesia':
        if province != 'Semua provinsi':
            raise ValueError('Pilih pulau/kawasan dahulu sebelum memilih provinsi')
        return []
    if island not in REGIONS:
        raise ValueError('Pulau/kawasan tidak dikenal')
    if province == 'Semua provinsi':
        return list(REGIONS[island])
    if province not in REGIONS[island]:
        raise ValueError('Provinsi bukan bagian dari pulau/kawasan yang dipilih')
    return [province]

def build_news_rss_urls(island='Seluruh Indonesia', province='Semua provinsi', keywords=None):
    """Build *region constrained* Google News search queries; no LLM involved.

    Island query is partitioned into province-sized searches: this avoids a single
    oversized OR expression and makes the search explicitly region-aware.
    Nationwide uses general Indonesian search terms (not a province filter).
    """
    terms = [s.strip() for s in (keywords or DEFAULT_TERMS) if s.strip()][:10]
    if not terms:
        terms = DEFAULT_TERMS
    # Quote multiword phrases and join with OR so varied LU topics are discoverable.
    topic = '(' + ' OR '.join('"' + t.replace('"','') + '"' for t in terms) + ')'
    provinces = selected_provinces(island, province)
    targets = provinces or ['Indonesia']
    result = []
    for target in targets:
        location = '"' + target + '"'
        query = f'{topic} {location}'
        result.append('https://news.google.com/rss/search?' + urlencode({
            'q':query, 'hl':'id', 'gl':'ID', 'ceid':'ID:id'
        }))
    return result


def region_for_province(province):
    for region, provinces in REGIONS.items():
        if province in provinces:
            return region
    return 'Tidak diketahui'
