from postbridge.services.markdown_plain import md_to_plain


def test_md_to_plain_removes_common_markdown_markup() -> None:
    assert md_to_plain(
        """
# Title

![alt](https://example.test/image.png)
[link text](https://example.test)
**bold** *italic* __strong__ _em_ `code`
> quote
---
""",
    ) == "Title\n\n!alt\nlink text\nbold italic strong em code\nquote"


def test_md_to_plain_returns_empty_for_blank_input() -> None:
    assert md_to_plain("") == ""
    assert md_to_plain("  \n ") == ""
