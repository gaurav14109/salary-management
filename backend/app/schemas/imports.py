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
    status: str = Field(description="PENDING, SUCCESS or FAILED.")
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
