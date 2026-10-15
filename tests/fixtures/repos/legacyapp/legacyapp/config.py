import yaml


def load(path):
    with open(path) as fh:
        return yaml.load(fh, Loader=yaml.FullLoader)
