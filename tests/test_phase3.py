"""Phase 3: SEO files, real 404s, pages served."""


def test_robots_and_sitemap(platform):
    doc = platform.doctor()
    platform.doctor(name="Dr. Hidden", approve=False)
    c = platform.client()
    robots = c.get("/robots.txt").text
    assert "Disallow: /api/" in robots and "Sitemap: http://pyshio.test/sitemap.xml" in robots
    sitemap = c.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert f"/doctors/{doc.slug}" in sitemap.text and "dr-hidden" not in sitemap.text


def test_real_404s(platform):
    c = platform.client()
    page = c.get("/no-such-page")
    assert page.status_code == 404 and "couldn't find that page" in page.text
    assert c.get("/doctors/nobody-here").status_code == 404
    api = c.get("/api/no-such-endpoint")
    assert api.status_code == 404 and api.json() == {"detail": "Not Found"}
    hidden = platform.doctor(name="Dr. Pending", approve=False)
    assert c.get(f"/doctors/{hidden.slug}").status_code == 404      # public can't see unapproved
    assert hidden.get(f"/doctors/{hidden.slug}").status_code == 200  # owner preview still works


def test_new_pages_and_favicon(platform):
    c = platform.client()
    for path in ["/privacy", "/terms", "/invoice"]:
        assert c.get(path).status_code == 200, path
    fav = c.get("/favicon.ico", follow_redirects=False)
    assert fav.status_code == 301 and fav.headers["location"] == "/assets/favicon.svg"
