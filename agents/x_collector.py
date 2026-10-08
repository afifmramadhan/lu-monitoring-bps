import csv
import io
import requests


def collect_x(query, bearer_token, limit=100):
    if not bearer_token:
        return []
    result = []
    next_token = None
    while len(result) < limit:
        params = {'query':query, 'max_results':min(100,max(10,limit-len(result))),
                  'tweet.fields':'created_at,author_id,lang'}
        if next_token:
            params['next_token'] = next_token
        res = requests.get('https://api.x.com/2/tweets/search/recent',
                           headers={'Authorization':f'Bearer {bearer_token}'},
                           params=params,timeout=30)
        res.raise_for_status()
        payload = res.json()
        for item in payload.get('data', []):
            result.append({'source':'x','title':'Postingan X','url':f"https://x.com/i/web/status/{item['id']}",
                           'text':item.get('text',''), 'published':item.get('created_at','')})
        next_token = payload.get('meta',{}).get('next_token')
        if not next_token or not payload.get('data'):
            break
    return result[:limit]


def import_x_csv(file):
    content = file.read()
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig')
    records = []
    for row in csv.DictReader(io.StringIO(content)):
        text = (row.get('text') or '').strip()
        url = (row.get('url') or '').strip()
        if text and url:
            records.append({'source':'x','title':row.get('title') or 'Postingan X',
                            'url':url,'text':text,'published':row.get('published','')})
    return records
