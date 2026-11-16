"""Entry point that assembles all command subgroups and flat commands into the top-level omop-alchemy Typer app."""

from __future__ import annotations

from typing import Annotated

import typer

from ..config import OmopAlchemyConfig
from . import (
    cli_backup as backup,
)
from . import (
    cli_foreign_keys as foreign_keys,
)
from . import (
    cli_fulltext as fulltext,
)
from . import (
    cli_indexes as indexes,
)
from . import (
    cli_schema as schema,
)
from . import (
    cli_tables as tables,
)
from . import (
    cli_vocab as vocab,
)
from .help import install_help_customizations
from .ui import console

install_help_customizations()

app = typer.Typer(
    help=(
        "OMOP Alchemy maintenance utilities.\n\n"
        "Some commands require backend-specific support — see individual command help for details."
    ),
    rich_markup_mode="rich",
)

# Subgroups
app.add_typer(foreign_keys.app, name="foreign-keys")
app.add_typer(indexes.app, name="indexes")
app.add_typer(fulltext.app, name="fulltext")

# Flat root-level commands lifted from each domain module
for _sub in (schema.app, vocab.app, tables.app, backup.app):
    for _cmd in _sub.registered_commands:
        app.registered_commands.append(_cmd)


@app.callback()
def _main(
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Increase log verbosity (-v INFO, -vv DEBUG). Must come before the subcommand name.",
        ),
    ] = 0,
) -> None:
    OmopAlchemyConfig.configure_logging(verbosity=verbose, console=console)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
