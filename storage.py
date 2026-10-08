import hashlib
import json
import sqlite3
from pathlib import Path


def doc_id(doc):
    return hashlib.sha256((doc['source']+'|'+doc['url']).encode('utf-8')).hexdigest()

class Store:
    def __init__(self, path='lu_monitoring.sqlite3'):
        self.conn = sqlite3.connect(path,check_same_thread=False)
        self.conn.execute('''CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, published TEXT,
            text TEXT, status TEXT DEFAULT 'pending', primary_lu TEXT,
            related TEXT, reason TEXT, needs_review INTEGER, summary TEXT, error TEXT)''')
        self.conn.commit()

    def add(self, docs):
        for d in docs:
            self.conn.execute('''INSERT OR IGNORE INTO documents
                (id,source,title,url,published,text) VALUES (?,?,?,?,?,?)''',
                (doc_id(d),d['source'],d['title'],d['url'],d.get('published',''),d['text']))
        self.conn.commit()

    def pending(self, limit=500):
        cur=self.conn.execute('SELECT id,source,title,url,published,text FROM documents WHERE status="pending" LIMIT ?', (limit,))
        return [dict(zip(['id','source','title','url','published','text'],row)) for row in cur.fetchall()]

    def finish(self, id, classification, summary):
        self.conn.execute('''UPDATE documents SET status=?,primary_lu=?,related=?,reason=?,needs_review=?,summary=?,error=NULL WHERE id=?''',
                          ('completed' if classification['relevant'] else 'irrelevant',
                           classification['primary'],json.dumps(classification['related']),
                           classification['reason'],int(classification['needs_review']),summary,id))
        self.conn.commit()

    def fail(self,id,error):
        self.conn.execute('UPDATE documents SET status="error",error=? WHERE id=?',(str(error)[:500],id))
        self.conn.commit()

    def rows(self):
        cur=self.conn.execute('SELECT source,title,url,published,status,primary_lu,related,reason,needs_review,summary,error FROM documents ORDER BY rowid DESC')
        cols=[x[0] for x in cur.description]
        return [dict(zip(cols,row)) for row in cur.fetchall()]
