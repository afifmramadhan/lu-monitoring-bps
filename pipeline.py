import time
from agents.classifier import classify, make_client
from agents.summarizer import summarize
from categories import CATEGORIES


def process(store, api_key, classify_model, summarize_model, limit=500, progress=None):
    client = make_client(api_key)
    pending = store.pending(limit)
    for idx, doc in enumerate(pending):
        try:
            classification = classify(client,classify_model,doc)
            summary = (summarize(client,summarize_model,doc,classification['primary'],
                                 CATEGORIES[classification['primary']])
                       if classification['relevant'] else '')
            store.finish(doc['id'],classification,summary)
        except Exception as exc:
            store.fail(doc['id'],exc)
        if progress:
            progress(idx+1,len(pending))
        if idx<len(pending)-1:
            time.sleep(0.6)  # Bukan pengganti penanganan rate limit tingkat produksi
    return len(pending)
