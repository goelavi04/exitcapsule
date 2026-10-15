import requests
from requests._internal_utils import to_native_string


class Session(requests.Session):
    pass


def fetch(url):
    r = requests.get(url, timeout=5)
    return to_native_string(r.text)
