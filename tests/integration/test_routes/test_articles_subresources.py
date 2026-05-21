"""Article sub-resource route tests — assets / sources / schema / publishes / eeat / drift."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_assets_full_cycle(api: TestClient, project_id: int, article_id: int) -> None:
    """Create + list + patch + delete an asset row, with generic artifact mirror."""
    resp = api.post(
        f"/api/v1/articles/{article_id}/assets",
        json={"kind": "hero", "url": "https://x.com/hero.png", "alt_text": "Hero"},
    )
    assert resp.status_code == 201
    asset_id = resp.json()["data"]["id"]
    rows = api.get(f"/api/v1/articles/{article_id}/assets").json()
    assert len(rows) == 1
    artifacts = api.get(
        f"/api/v1/projects/{project_id}/artifacts",
        params={"kind": "article-asset"},
    ).json()["items"]
    assert len(artifacts) == 1
    assert artifacts[0]["uri"] == "https://x.com/hero.png"
    assert artifacts[0]["provenance_json"] == {
        "source_table": "article_assets",
        "source_id": asset_id,
        "article_id": article_id,
        "prompt": None,
    }

    patch = api.patch(
        f"/api/v1/articles/{article_id}/assets/{asset_id}",
        json={"alt_text": "Updated alt"},
    )
    assert patch.status_code == 200
    assert patch.json()["data"]["alt_text"] == "Updated alt"

    deleted = api.delete(f"/api/v1/articles/{article_id}/assets/{asset_id}")
    assert deleted.status_code == 200
    rows = api.get(f"/api/v1/articles/{article_id}/assets").json()
    assert len(rows) == 0


def test_sources_create_and_list(api: TestClient, article_id: int) -> None:
    """Citation rows are listable in id order."""
    api.post(
        f"/api/v1/articles/{article_id}/sources",
        json={"url": "https://example.com/a", "title": "A"},
    )
    api.post(
        f"/api/v1/articles/{article_id}/sources",
        json={"url": "https://example.com/b", "title": "B"},
    )
    rows = api.get(f"/api/v1/articles/{article_id}/sources").json()
    assert [r["url"] for r in rows] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_schema_set_is_primary_invariant(api: TestClient, article_id: int) -> None:
    """Two ``is_primary=true`` schema rows — only one stays primary."""
    api.put(
        f"/api/v1/articles/{article_id}/schema/Article",
        json={"schema_json": {"@type": "Article"}, "is_primary": True},
    )
    api.put(
        f"/api/v1/articles/{article_id}/schema/Product",
        json={"schema_json": {"@type": "Product"}, "is_primary": True},
    )
    rows = api.get(f"/api/v1/articles/{article_id}/schema").json()
    primaries = [r for r in rows if r["is_primary"]]
    assert len(primaries) == 1


def test_versions_list_starts_empty(api: TestClient, article_id: int) -> None:
    """No versions until ``create_version`` is called."""
    resp = api.get(f"/api/v1/articles/{article_id}/versions")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_create_version_snapshots_row(api: TestClient, article_id: int) -> None:
    """``create_version`` snapshots the live row + bumps ``article.version``."""
    resp = api.post(f"/api/v1/articles/{article_id}/version")
    assert resp.status_code == 201
    snap = resp.json()["data"]
    assert snap["article_id"] == article_id
    rows = api.get(f"/api/v1/articles/{article_id}/versions").json()["items"]
    assert len(rows) == 1


def test_eeat_report_empty_when_no_evaluations(api: TestClient, article_id: int) -> None:
    """``GET /eeat`` returns a zeroed report when no evaluations exist."""
    resp = api.get(f"/api/v1/articles/{article_id}/eeat")
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"]["total_evaluations"] == 0
    assert body["evaluations"] == []


def test_drift_baseline_snapshot(api: TestClient, article_id: int) -> None:
    """Snapshot creates a baseline row visible via list."""
    resp = api.post(
        f"/api/v1/articles/{article_id}/drift/snapshot",
        json={"baseline_md": "# Baseline"},
    )
    assert resp.status_code == 201
    rows = api.get(f"/api/v1/articles/{article_id}/drift").json()
    assert len(rows) == 1


def test_publishes_record_and_set_canonical(
    api: TestClient, project_id: int, article_id: int
) -> None:
    """Record a publish + flip ``canonical_target_id``."""
    target = api.post(
        f"/api/v1/projects/{project_id}/publish-targets",
        json={
            "kind": "hugo",
            "config_json": {"repo_path": "/tmp/hugo-site", "content_subdir": "content"},
            "is_primary": True,
        },
    ).json()["data"]
    pub = api.post(
        f"/api/v1/articles/{article_id}/publishes",
        json={
            "target_id": target["id"],
            "version_published": 1,
            "published_url": "https://a.com/x",
        },
    )
    assert pub.status_code == 201
    canonical = api.post(
        f"/api/v1/articles/{article_id}/publishes/canonical",
        json={"target_id": target["id"]},
    )
    assert canonical.status_code == 200
    assert canonical.json()["data"]["canonical_target_id"] == target["id"]


def test_external_publish_record(api: TestClient, article_id: int) -> None:
    """Targetless external publish rows do not require a publish target."""
    pub = api.post(
        f"/api/v1/articles/{article_id}/publishes/external",
        json={
            "version_published": 1,
            "published_url": "https://a.com/external",
            "external_ref": "post-123",
            "frontmatter_json": {"external_path": "db-direct"},
        },
    )
    assert pub.status_code == 201
    body = pub.json()["data"]
    assert body["target_id"] is None
    assert body["published_url"] == "https://a.com/external"
    assert body["frontmatter_json"] == {
        "publisher": "agent",
        "external_path": "db-direct",
        "external_ref": "post-123",
    }


def test_article_interlinks_report(api: TestClient, project_id: int, article_id: int) -> None:
    """``GET /articles/{id}/interlinks`` returns both directions."""
    # Need a second article to point at.
    a2 = api.post(
        f"/api/v1/projects/{project_id}/articles",
        json={"title": "Other", "slug": "other"},
    ).json()["data"]
    api.post(
        f"/api/v1/projects/{project_id}/interlinks",
        json={
            "from_article_id": article_id,
            "to_article_id": a2["id"],
            "anchor_text": "see also",
        },
    )
    rep = api.get(f"/api/v1/articles/{article_id}/interlinks")
    assert rep.status_code == 200
    body = rep.json()
    assert len(body["outgoing"]) == 1
    assert body["incoming"] == []
