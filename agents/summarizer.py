from google.genai import types


def summarize(client, model, doc, primary, category_name):
    response = client.models.generate_content(
        model=model,
        contents=(f'Ringkas dalam bahasa Indonesia maksimum 90 kata untuk monitoring LU BPS {primary} - {category_name}. '
                  'Sebut apa yang terjadi, wilayah dan angka bila benar-benar tersedia, serta implikasi ekonomi yang dinyatakan '
                  'atau secara hati-hati dapat ditarik dari teks. Bedakan fakta dari indikasi. '
                  'Jangan mengarang dan jangan menganggap satu berita sebagai bukti tren makro. '
                  f"Judul: {doc['title']}\nTeks: {doc['text'][:9000]}"),
        config=types.GenerateContentConfig(temperature=0.2))
    return response.text or ''
