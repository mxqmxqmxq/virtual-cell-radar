from aivc.mdhtml import render


def test_headings_and_paragraph():
    h = render("# A\n\n#### D\n\nhello\nworld")
    assert "<h1>A</h1>" in h and "<h4>D</h4>" in h
    assert "<p>hello<br>world</p>" in h


def test_inline():
    h = render("**b** `c<d>` [x](https://a.com/?q=1&r=2)")
    assert "<strong>b</strong>" in h
    assert "<code>c&lt;d&gt;</code>" in h
    assert 'href="https://a.com/?q=1&amp;r=2"' in h
    assert 'rel="noopener noreferrer"' in h and 'target="_blank"' in h


def test_lists_quote_hr():
    h = render("- a\n* b\n\n1. x\n2. y\n\n> q1\n> q2\n\n---")
    assert "<ul><li>a</li><li>b</li></ul>" in h
    assert "<ol><li>x</li><li>y</li></ol>" in h
    assert "<blockquote>" in h and "q1" in h and "q2" in h
    assert "<hr>" in h


def test_table_with_escaped_pipe():
    h = render("| 名称 | 值 |\n|---|---|\n| a\\|b | **1** |\n| c | 2 |")
    assert "<th>名称</th>" in h
    assert "<td>a|b</td>" in h
    assert "<td><strong>1</strong></td>" in h
    assert h.count("<tr>") == 3


def test_script_escaped():
    h = render("<script>alert(1)</script>\n\n# <img src=x onerror=1>")
    assert "<script" not in h and "<img" not in h
    assert "&lt;script&gt;" in h


def test_javascript_link_not_linked():
    h = render("[x](javascript:alert(1)) [y](data:text/html,hi)")
    assert "href" not in h
    assert "[x](javascript:alert(1))" in h


def test_unbalanced_markers_kept():
    h = render("a **b and `c")
    assert "**b" in h and "`c" in h and "<strong>" not in h


def test_empty_and_weird():
    assert render("") == ""
    assert render("\x00\x00 |||\n|---|") is not None
    assert render("|\n|-|") is not None


def test_heading_keeps_trailing_hash_in_text():
    from aivc.mdhtml import render as _r
    assert "C#" in _r("### 1. Learning with C#")
    assert "##" not in _r("## 标题 ##")
