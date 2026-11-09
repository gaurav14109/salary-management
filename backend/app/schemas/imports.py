"""The import contract.

The error list is the important part. A failed import whose response says only
"failed" sends someone back to a ten-thousand-row spreadsheet with no idea where to
look.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ImportRowError(BaseModel):
    """One rejected cell, addressed the way the user sees it.

    `row_number` is the line number in the spreadsheet, header included, so it
    matches what Excel shows in the margin. `row_number: 0` means the problem is the
    file itself - a missing column, an empty sheet - not any particular line.
    """

    model_config = ConfigDict(frozen=True)

    row_number: int = Field(description="Spreadsheet line, header counted. 0 = the whole file.")
    field: str
    message: str


class ImportJobRead(BaseModel):
    """What happened to one upload.

    The counts are deliberately granular. "1,000 rows succeeded" hides the thing
    people actually ask after an import: how many got a raise, and how many were
    already correct.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    status: str = Field(
        description=(
            "PENDING (queued), PROCESSING (being applied), SUCCESS, FAILED or "
            "CANCELLED. An upload returns PENDING; poll this endpoint for the rest."
        )
    )
    # Why it failed when the answer is not a row error - an unreadable file, a
    # crash. `errors` carries the per-row detail and says nothing about these.
    failure_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_rows: int
    success_rows: int
    error_rows: int
    employees_created: int
    employees_updated: int
    salaries_created: int
    salaries_unchanged: int = Field(
        description="Rows whose pay already matched. No row written, no audit entry - "
        "this is what makes re-uploading an exported file safe."
    )
    errors: list[ImportRowError]
    created_by: int
    created_at: datetime


class ImportPreviewRow(BaseModel):
    """One line of the file, still as text.

    Values stay strings on the way out as well as in. The grid is editing a
    spreadsheet: a date somebody typed as `01/04/2024` has to come back exactly as
    they typed it, or they cannot see what is wrong with it. Parsing here and
    re-rendering would quietly correct some mistakes and mangle others.
    """

    model_config = ConfigDict(frozen=True)

    row_number: int = Field(description="Spreadsheet line, header counted.")
    values: dict[str, str]


class ImportPreview(BaseModel):
    """What a file would do, without doing it.

    Returned by both preview endpoints, so the page renders the same thing whether
    the rows came from a file or from the user's own edits. The errors are the same
    errors the import would produce - the identical `validate` call - because a
    preview that reported less than the import would promise a clean run and then
    fail.
    """

    columns: list[str] = Field(description="Header row, in file order. The grid's columns.")
    rows: list[ImportPreviewRow]
    errors: list[ImportRowError]
    row_count: int = Field(description="Rows in the file, before any truncation.")
    error_count: int
    truncated: bool = Field(
        description="True when the file has more rows than the grid will show. The "
        "rows are a sample; the file is still importable in full."
    )
