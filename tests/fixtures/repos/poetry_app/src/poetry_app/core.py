import attr
import toml
import requests


@attr.s
class Thing:
    x = attr.ib(default=1)


def read(p):
    return toml.load(p), requests.__version__
