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


def test_ask_loose_match(tmp_path):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import kuzu
    from skg.web.app import app
    from skg.query import QueryRequest

    # Create a temp Kuzu database and seed it with 'folate'
    db_path = tmp_path / "test_kg.kuzu"
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    conn.execute("CREATE NODE TABLE Compound (name STRING, PRIMARY KEY(name))")
    conn.execute("MERGE (c:Compound {name: 'folate'})")

    # Temporarily point the app state's db to our test db
    old_db = getattr(app.state, "db", None)
    app.state.db = db

    try:
        client = TestClient(app)
        mock_req = QueryRequest(query="compound", entity="methylfolate", min_evidence=1)

        with patch("skg.router.route", return_value=mock_req), \
             patch("skg.query.dispatch", return_value=[]), \
             patch("skg.summarize.summarize", return_value=""), \
             patch("skg.web.app.is_compound_ingested", return_value=False):
            response = client.post("/ask", data={"q": "what does methylfolate do?"})

        assert response.status_code == 200
        html = response.text
        assert "I matched <strong>methylfolate</strong> to <strong>folate</strong>" in html
        assert "Did you mean <strong>folate</strong>" in html
        assert "would you like to fetch and ingest <strong>methylfolate</strong> specifically?" in html
        assert "Yes, Ingest methylfolate" in html
        assert "No, Ingest folate" in html
    finally:
        if old_db is not None:
            app.state.db = old_db


def test_ingest_canonicalisation_trigger(tmp_path):
    from unittest.mock import patch, AsyncMock
    from fastapi.testclient import TestClient
    import kuzu
    from skg.web.app import app

    # Create a temp Kuzu database and seed it with 'folate'
    db_path = tmp_path / "test_kg.kuzu"
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    from skg import graph
    graph.init_schema(conn)
    conn.execute("MERGE (c:Compound {name: 'folate'})")

    # Temporarily point the app state's db to our test db
    old_db = getattr(app.state, "db", None)
    app.state.db = db

    try:
        client = TestClient(app)
        
        with patch("skg.web.app.is_compound_ingested", return_value=False), \
             patch("skg.web.app.ingest_supplement_async", new_callable=AsyncMock) as mock_ingest, \
             patch("skg.web.app.propose", return_value={}) as mock_propose, \
             patch("skg.web.app.apply_map", return_value={}) as mock_apply:
            
            response = client.post("/ingest", data={"supplement": "folate"})
            
            assert response.status_code == 200
            mock_ingest.assert_called_once()
            mock_propose.assert_called_once()
            mock_apply.assert_called_once()
    finally:
        if old_db is not None:
            app.state.db = old_db


def test_canonicalise_endpoint(tmp_path):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import kuzu
    from skg.web.app import app

    # Create a temp Kuzu database
    db_path = tmp_path / "test_kg.kuzu"
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    from skg import graph
    graph.init_schema(conn)

    # Temporarily point the app state's db to our test db
    old_db = getattr(app.state, "db", None)
    app.state.db = db

    try:
        client = TestClient(app)
        
        mock_result = {"Compound": 2, "Target": 0, "Effect": 0}
        with patch("skg.web.app.propose", return_value={}) as mock_propose, \
             patch("skg.web.app.apply_map", return_value=mock_result) as mock_apply:
            
            response = client.post("/canonicalise")
            
            assert response.status_code == 200
            mock_propose.assert_called_once()
            mock_apply.assert_called_once()
            assert "Graph deduplicated successfully" in response.text
            assert "Compound: 2 merged" in response.text
    finally:
        if old_db is not None:
            app.state.db = old_db



