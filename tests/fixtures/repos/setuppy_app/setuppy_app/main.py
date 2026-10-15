"""Usage: app <name>"""
from docopt import docopt
from dateutil import parser as dparser
import six


def run(argv=None):
    args = docopt(__doc__, argv=argv)
    return six.text_type(args["<name>"]), dparser.parse("2020-01-01")
