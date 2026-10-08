from categories import CATEGORIES
from storage import Store

def test_categories():
    assert len(CATEGORIES) == 17
    assert CATEGORIES['MN'] == 'Jasa Perusahaan'

def test_store():
    s=Store(':memory:')
    item={'source':'news','title':'Tes','url':'https://example.com/a','text':'Pertanian'}
    s.add([item,item])
    assert len(s.pending()) == 1
    s.finish(s.pending()[0]['id'],{'relevant':True,'primary':'A','related':[],'reason':'tes','needs_review':False},'Ringkasan')
    assert s.rows()[0]['status']=='completed'
