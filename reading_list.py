"""Personal reading list serialization. No shared server file storage."""
import hashlib
import json

FIELDS = ('identifier','source','title','url','abstract','summary','published','year','doi','pmid','validation_method','validation_reason','status')


def article_key(item):
    identity = str(item.get('source') or '') + ':' + str(item.get('identifier') or item.get('url') or '')
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def set_flag(collection, item, flag, value):
    if flag not in ('saved','read'):
        raise ValueError('Unknown flag')
    key = article_key(item)
    old = collection.get(key, {})
    entry = {field: str(item.get(field) or '') for field in FIELDS}
    entry['matched_topics'] = list(item.get('matched_topics') or [])
    entry['saved'] = bool(old.get('saved', False))
    entry['read'] = bool(old.get('read', False))
    entry[flag] = bool(value)
    if entry['saved'] or entry['read']:
        collection[key] = entry
    else:
        collection.pop(key, None)


def export_collection(collection):
    return json.dumps({'version':1,'articles':list(collection.values())},ensure_ascii=False,indent=2)


def import_collection(raw):
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('Размер файла не должен превышать 5 МБ.')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('Не удалось прочитать JSON.') from None
    if not isinstance(data,dict) or data.get('version') != 1 or not isinstance(data.get('articles'),list):
        raise ValueError('Неизвестный формат подборки.')
    if len(data['articles']) > 5000:
        raise ValueError('В файле допускается до 5000 записей.')
    result={}
    for item in data['articles']:
        if not isinstance(item,dict):
            raise ValueError('Некорректная запись.')
        if not all(isinstance(item.get(f,''),str) for f in FIELDS):
            raise ValueError('Поля статьи должны содержать текст.')
        if not item.get('identifier') or not item.get('source'):
            raise ValueError('Отсутствует идентификатор или источник.')
        if not str(item.get('url','')).startswith(('https://','http://')):
            raise ValueError('Некорректная ссылка статьи.')
        topics=item.get('matched_topics',[])
        if not isinstance(topics,list) or any(not isinstance(t,str) for t in topics):
            raise ValueError('Некорректный список тем.')
        for flag in ('saved','read'):
            if not isinstance(item.get(flag,False),bool):
                raise ValueError('Некорректная отметка статьи.')
        clean={f:item.get(f,'') for f in FIELDS}
        clean.update(matched_topics=topics,saved=item.get('saved',False),read=item.get('read',False))
        if clean['saved'] or clean['read']:
            result[article_key(clean)]=clean
    return result
