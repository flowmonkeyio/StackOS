from __future__ import annotations

from pathlib import Path

from stackos.mcp.tools.guides import (
    GETTING_STARTED_GUIDE_URL,
    GETTING_STARTED_MARKDOWN_URL,
)

ROOT = Path(__file__).resolve().parents[2]


def test_getting_started_references_share_one_public_source() -> None:
    desktop_main = (ROOT / "desktop" / "src" / "main.js").read_text(encoding="utf-8")
    ui_links = (ROOT / "ui" / "src" / "lib" / "externalLinks.ts").read_text(encoding="utf-8")
    guide = (ROOT / "website" / "content" / "guides" / "getting-started.md").read_text(
        encoding="utf-8"
    )
    apache_headers = (ROOT / "website" / "public" / ".htaccess").read_text(encoding="utf-8")

    assert f'const GETTING_STARTED_URL = "{GETTING_STARTED_GUIDE_URL}";' in desktop_main
    assert f"GETTING_STARTED_URL = '{GETTING_STARTED_GUIDE_URL}'" in ui_links
    assert f"canonicalUrl: {GETTING_STARTED_GUIDE_URL}" in guide
    assert f"markdownUrl: {GETTING_STARTED_MARKDOWN_URL}" in guide
    assert f"<{GETTING_STARTED_GUIDE_URL}>;" in apache_headers
    assert 'Header set X-Robots-Tag "noindex"' in apache_headers
