import click
import pkg_resources

from .config import load


@click.command()
@click.option("--path", default="cfg.yml")
def main(path):
    version = pkg_resources.get_distribution("legacyapp").version
    click.echo(f"{version} {load(path)}")
