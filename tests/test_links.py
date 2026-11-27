from aivc.fetchers.links import (
    ExtractedLinks,
    clean_url,
    extract_links,
    find_code_url,
    find_dataset_url,
    find_weights_url,
    has_release_context,
)


def test_github_trailing_punct():
    assert find_code_url("Code at https://github.com/foo/bar.") == "https://github.com/foo/bar"
    assert find_code_url("(see https://github.com/foo/bar)") == "https://github.com/foo/bar"
    assert find_code_url("https://github.com/Foo/Bar-x, and more") == "https://github.com/Foo/Bar-x"


def test_github_www_and_git_suffix():
    assert find_code_url("http://www.github.com/a/b.git") == "https://github.com/a/b"


def test_gitlab():
    assert find_code_url("see https://gitlab.com/grp/proj;") == "https://gitlab.com/grp/proj"


def test_org_page_ignored():
    assert find_code_url("https://github.com/openai") is None
    assert find_code_url("https://github.com/openai/") is None


def test_reserved_owner_ignored():
    assert find_code_url("https://github.com/topics/single-cell") is None
    assert find_code_url("https://github.com/orgs/foo/people") is None


def test_reserved_then_real():
    t = "https://github.com/topics/x and https://github.com/real/repo"
    assert find_code_url(t) == "https://github.com/real/repo"


def test_tree_subpath_truncated():
    assert (
        find_code_url("https://github.com/a/b/tree/main/src")
        == "https://github.com/a/b"
    )
    assert find_code_url("https://github.com/a/b/blob/x/y.py") == "https://github.com/a/b"


def test_hf_model_dataset_space():
    assert find_weights_url("weights: https://huggingface.co/org/model.") == "https://huggingface.co/org/model"
    assert find_weights_url("https://huggingface.co/datasets/o/d") is None
    assert (
        find_dataset_url("https://huggingface.co/datasets/org/data")
        == "https://huggingface.co/datasets/org/data"
    )
    sp = "https://huggingface.co/spaces/org/demo)"
    assert find_weights_url(sp) is None and find_dataset_url(sp) is None


def test_hf_non_repo_paths_ignored():
    assert find_weights_url("https://huggingface.co/papers/2401.00001") is None
    assert find_weights_url("https://huggingface.co/blog/foo") is None
    assert find_weights_url("https://huggingface.co/docs/transformers") is None
    assert find_weights_url("https://huggingface.co/collections/a/b") is None
    assert find_weights_url("https://huggingface.co/someorg") is None


def test_zenodo_records_and_doi():
    assert find_dataset_url("data https://zenodo.org/records/1234567.") == "https://zenodo.org/records/1234567"
    assert find_dataset_url("https://zenodo.org/record/987") == "https://zenodo.org/records/987"
    assert (
        find_dataset_url("doi: https://doi.org/10.5281/zenodo.555)")
        == "https://doi.org/10.5281/zenodo.555"
    )


def test_figshare_subdomain_and_paths():
    u = "https://springernature.figshare.com/articles/dataset/foo/12345."
    assert find_dataset_url(u) == "https://springernature.figshare.com/articles/dataset/foo/12345"
    assert find_dataset_url("https://figshare.com/s/abcdef123") == "https://figshare.com/s/abcdef123"
    assert find_dataset_url("https://figshare.com/") is None


def test_multi_text_order():
    r = extract_links(
        None,
        "first https://github.com/a/one",
        "second https://github.com/b/two https://zenodo.org/records/42",
    )
    assert r.code_url == "https://github.com/a/one"
    assert r.dataset_url == "https://zenodo.org/records/42"
    assert r.weights_url is None
    assert not r.has_code  # no release context in the sample text


def test_all_empty():
    assert extract_links() == ExtractedLinks(None, None, False, False)
    assert extract_links(None, "", "no links here") == ExtractedLinks(None, None, False, False)
    assert find_code_url("") is None
    assert find_weights_url("") is None


def test_clean_url():
    assert clean_url('"https://x.org/a/b/".') == "https://x.org/a/b"
    assert clean_url("https://github.com/a/b.git)") == "https://github.com/a/b"


def test_release_context_positive():
    r = extract_links("Code is available at https://github.com/a/b")
    assert r.code_url == "https://github.com/a/b" and r.has_code
    r = extract_links("We release weights at https://huggingface.co/org/m")
    assert r.weights_url == "https://huggingface.co/org/m" and r.has_weights
    r = extract_links("Data deposited at https://zenodo.org/records/1")
    assert r.dataset_url == "https://zenodo.org/records/1" and r.has_dataset


def test_release_context_negative():
    r = extract_links("We fine-tune scGPT (https://github.com/bowang-lab/scGPT) on our data.")
    assert r.code_url == "https://github.com/bowang-lab/scGPT" and not r.has_code
    r = extract_links("Our method is based on the implementation from https://github.com/x/y")
    assert r.code_url == "https://github.com/x/y" and not r.has_code
    r = extract_links("using https://huggingface.co/org/m as backbone")
    assert r.weights_url == "https://huggingface.co/org/m" and not r.has_weights


def test_negative_overrides_positive_cue():
    t = "Code is based on the implementation from https://github.com/x/y"
    assert not extract_links(t).has_code


def test_later_evidenced_url_preferred():
    t = "Built on https://github.com/x/y. Our code is available at https://github.com/me/mine"
    r = extract_links(t)
    assert r.code_url == "https://github.com/me/mine" and r.has_code


def test_no_cue_no_evidence():
    t = "see https://github.com/a/b"
    assert not has_release_context(t, 4, len(t))
    assert extract_links("") == ExtractedLinks(None, None, False, False, None, False)
