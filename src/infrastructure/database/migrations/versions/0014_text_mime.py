"""files: text MIME types for documents stored as application/octet-stream

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TEXT_TYPES = {
    "txt": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "xml": "text/xml",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "html": "text/html",
    "htm": "text/html",
    "rtf": "text/rtf",
    "css": "text/css",
}


def upgrade() -> None:
    for ext, mime in TEXT_TYPES.items():
        op.execute(
            sa.text(
                "UPDATE files SET mime = :mime WHERE mime = 'application/octet-stream'"
                " AND kind = 'document' AND lower(name) LIKE :pattern"
            ).bindparams(mime=mime, pattern=f"%.{ext}")
        )


def downgrade() -> None:
    pass
