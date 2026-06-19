from __future__ import annotations

from skg.web.app import render_markdown, templates


def test_render_markdown():
    # Test bold rendering
    assert render_markdown("Hello **world**") == "<p>Hello <strong>world</strong></p>"

    # Test bullet lists
    text = (
        "Based on claims:\n"
        "* **Alzheimer's disease** is decreased.\n"
        "* **Cancer** is decreased."
    )
    expected = (
        "<p>Based on claims:</p>\n"
        "<ul>\n"
        "<li><strong>Alzheimer's disease</strong> is decreased.</li>\n"
        "<li><strong>Cancer</strong> is decreased.</li>\n"
        "</ul>"
    )
    assert render_markdown(text) == expected


def test_filter_registered():
    assert "markdown" in templates.env.filters
    assert templates.env.filters["markdown"] is render_markdown
