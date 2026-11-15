from aivc.textutil import biorxiv_id_from_doi, normalize_doi, normalize_title, strip_arxiv_version


def test_normalize_title():
    assert normalize_title("  Foo-Bar: $\\alpha$ {Cell}\n Model! ") == "foo bar alpha cell model"
    assert normalize_title("ＡＢＣ") == "abc"


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1101/2026.01.02.123456v2") == "10.1101/2026.01.02.123456"
    assert normalize_doi("doi:10.1038/S41586-024") == "10.1038/s41586-024"


def test_strip_arxiv_version():
    assert strip_arxiv_version("2606.01042v2") == "2606.01042"
    assert strip_arxiv_version("2606.01042") == "2606.01042"


def test_biorxiv_id():
    assert biorxiv_id_from_doi("10.1101/2026.07.15.738608v1") == "2026.07.15.738608"
    assert biorxiv_id_from_doi("10.64898/2026.07.15.738608") == "2026.07.15.738608"
    assert biorxiv_id_from_doi("10.1038/s41586") is None
    assert biorxiv_id_from_doi(None) is None
