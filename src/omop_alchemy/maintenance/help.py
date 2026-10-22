from __future__ import annotations

from typing import Literal, cast

import click
import typer.rich_utils as typer_rich_utils
from rich import box
from rich.box import Box
from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_BACKEND_NOTE_MARKER = "Supported backends:"


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _strip_backend_flag(helptext: str) -> tuple[str, str | None]:
    """Split off a trailing 'Supported backends: ...' note, if present."""
    marker = f". {_BACKEND_NOTE_MARKER}"
    idx = helptext.rfind(marker)
    if idx != -1:
        tail = helptext[idx + len(marker):]
        if ". " not in tail:  # guard: note must be the final sentence
            return helptext[:idx], helptext[idx + 2:]  # skip leading ". "
    if helptext.startswith(_BACKEND_NOTE_MARKER):
        return "", helptext
    return helptext, None


def _print_commands_panel_with_backend_grouping(
    *,
    name: str,
    commands: list[click.Command],
    markup_mode: Literal["markdown", "rich"],
    console: Console,
    cmd_len: int,
) -> None:
    box_name = str(typer_rich_utils.STYLE_COMMANDS_TABLE_BOX)
    box_style = (
        cast(Box | None, getattr(box, box_name, None))
        if box_name
        else None
    )

    commands_table = Table(
        highlight=False,
        show_header=False,
        expand=True,
        box=box_style,
        show_lines=bool(typer_rich_utils.STYLE_COMMANDS_TABLE_SHOW_LINES),
        leading=int(typer_rich_utils.STYLE_COMMANDS_TABLE_LEADING),
        border_style=_optional_str(typer_rich_utils.STYLE_COMMANDS_TABLE_BORDER_STYLE),
        row_styles=typer_rich_utils.STYLE_COMMANDS_TABLE_ROW_STYLES,
        pad_edge=bool(typer_rich_utils.STYLE_COMMANDS_TABLE_PAD_EDGE),
        padding=typer_rich_utils.STYLE_COMMANDS_TABLE_PADDING,
    )
    commands_table.add_column(
        style=typer_rich_utils.STYLE_COMMANDS_TABLE_FIRST_COLUMN,
        no_wrap=True,
        width=cmd_len,
    )
    commands_table.add_column("Description", justify="left", no_wrap=False, ratio=10)

    deprecated_rows: list[RenderableType | None] = []
    rows: list[tuple[list[RenderableType | None], bool]] = []

    for command in commands:
        helptext = command.short_help or command.help or ""
        helptext, backend_flag = _strip_backend_flag(helptext)

        if command.deprecated:
            command_name_text = Text(
                command.name or "",
                style=typer_rich_utils.STYLE_DEPRECATED_COMMAND,
            )
            deprecated_rows.append(
                Text(
                    typer_rich_utils.DEPRECATED_STRING,
                    style=typer_rich_utils.STYLE_DEPRECATED,
                )
            )
        else:
            command_name_text = Text(command.name or "")
            deprecated_rows.append(None)

        help_renderable = typer_rich_utils._make_command_help(
            help_text=helptext,
            markup_mode=markup_mode,
        )
        if backend_flag and isinstance(help_renderable, Text):
            help_renderable.append(" ")
            help_renderable.append(backend_flag, style="red")

        rows.append(
            (
                [command_name_text, help_renderable],
                backend_flag is not None,
            )
        )

    portable_rows = [
        row
        for row, is_backend_specific in rows
        if not is_backend_specific
    ]
    flagged_rows = [
        row
        for row, is_backend_specific in rows
        if is_backend_specific
    ]

    ordered_rows = portable_rows[:]
    if portable_rows and flagged_rows:
        ordered_rows.append([Text(""), Text("")])
    ordered_rows.extend(flagged_rows)

    if any(deprecated_rows):
        ordered_deprecated_rows = [
            deprecated
            for deprecated, (_, is_backend_specific) in zip(
                deprecated_rows,
                rows,
                strict=True,
            )
            if not is_backend_specific
        ]
        flagged_deprecated_rows = [
            deprecated
            for deprecated, (_, is_backend_specific) in zip(
                deprecated_rows,
                rows,
                strict=True,
            )
            if is_backend_specific
        ]
        if portable_rows and flagged_rows:
            ordered_deprecated_rows.append(None)
        ordered_deprecated_rows.extend(flagged_deprecated_rows)
        for row, deprecated_text in zip(
            ordered_rows,
            ordered_deprecated_rows,
            strict=True,
        ):
            row.append(deprecated_text)

    for row in ordered_rows:
        commands_table.add_row(*row)

    if commands_table.row_count:
        console.print(
            Panel(
                commands_table,
                border_style=typer_rich_utils.STYLE_COMMANDS_PANEL_BORDER,
                title=name,
                title_align=typer_rich_utils.ALIGN_COMMANDS_PANEL,
            )
        )


def install_help_customizations() -> None:
    typer_rich_utils._print_commands_panel = _print_commands_panel_with_backend_grouping  # ty: ignore[invalid-assignment]
