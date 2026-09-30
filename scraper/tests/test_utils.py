from scraper.utils import canonical_url, slugify, stable_id


def test_canonical_url_drops_fragment_and_normalizes_host():
    assert canonical_url("HTTPS://WWW.BIS.GOV.IN//x.pdf?a=1#page=2") == "https://www.bis.gov.in/x.pdf?a=1"


def test_stable_id_and_slug():
    assert stable_id("https://bis.gov.in/x") == stable_id("https://bis.gov.in/x#top")
    assert slugify("IS 4250:2025 / Product Manual") == "is-4250-2025-product-manual"

