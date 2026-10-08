import json
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from categories import CATEGORIES

class Classification(BaseModel):
    primary: str = Field(description='Satu kode kategori utama: A sampai L, MN, O, P, Q, RSTU; atau NONE')
    related: list[str]
    relevant: bool
    reason: str
    needs_review: bool


def classify(client, model, doc):
    instructions = ('Klasifikasikan artikel/postingan untuk monitoring ekonomi Indonesia menggunakan 17 LU BPS. '
                    'Gunakan fokus kegiatan ekonomi, bukan sekadar kata kunci. '
                    'Pilih primary tepat satu kode valid, atau NONE jika tidak relevan/tidak cukup informasi. '
                    'related maksimal 3 kode berbeda dari primary; jangan mengarang. '
                    'Jika ambigu needs_review=true. Kategori: ' + json.dumps(CATEGORIES,ensure_ascii=False))
    response = client.models.generate_content(
        model=model,
        contents=f"{instructions}\nJUDUL: {doc['title']}\nSUMBER: {doc['source']}\nTEKS:\n{doc['text'][:9000]}",
        config=types.GenerateContentConfig(response_mime_type='application/json',
                                           response_schema=Classification,temperature=0))
    result = Classification.model_validate_json(response.text).model_dump()
    codes = set(CATEGORIES)
    if result['primary'] not in codes | {'NONE'}:
        raise ValueError('Kategori utama AI tidak valid')
    result['related'] = [v for v in result['related'] if v in codes and v != result['primary']][:3]
    if result['primary'] == 'NONE':
        result['relevant'] = False
        result['needs_review'] = True
    return result


def make_client(api_key):
    return genai.Client(api_key=api_key)
