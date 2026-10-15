from setuptools import setup

REQUIRES = ["docopt==0.6.2", "six"]
EXTRAS = {"test": ["pytest"], "yaml": ["pyyaml>=5"]}

setup(
    name="setuppy_app",
    install_requires=REQUIRES + ["python-dateutil<2.8"],
    extras_require=EXTRAS,
    python_requires=">=2.7",
)
