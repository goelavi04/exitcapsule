import imp

try:
    import ujson as json
except ImportError:
    import json


def loads(s):
    return json.loads(s)
