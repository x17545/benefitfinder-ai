"""Fictional government API values; no real policy facts or approvals."""


def gov24_fields(key: str = 'A', *, support: str = '가상: 청년에게 20만 원 지원') -> dict:
    name = '가상 청년 취업 ' + key
    return {
        'serviceList': {'서비스ID': key, '서비스명': name,
                        '서비스분야': 'fictional-employment', '조회수': 3},
        'serviceDetail': {'서비스ID': key, '서비스명': name, '지원내용': support,
                          '지원대상': '가상: 만 19~39세', '문의처': 'PRIVATE NOT FOR LLM'},
        'supportConditions': {'서비스ID': key, 'JA0110': 19, 'JA0111': 39},
    }


def gov24_plan(*keys: str) -> dict:
    keys = keys or ('A',)
    return {'format_version': 1, 'source': 'gov24',
        'scope': {'category_field': 'serviceCategory', 'category_values': {
            'employment': ['fictional-employment'], 'housing': ['fictional-housing']}},
        'policies': [{'id': 'youth:fictional:' + key, 'name': '가상 청년 취업 ' + key,
                      'categories': ['employment'], 'aliases': []} for key in keys],
        'targets': [{'policy_id': 'youth:fictional:' + key, 'documents': [{
            'external_id': key, 'name_field': 'serviceName',
            'body_fields': ['detailTarget', 'detailSupport', 'ageStart', 'ageEnd'],
            'scope_guards': {'serviceCategory': 'fictional-employment'},
        }]} for key in keys]}
