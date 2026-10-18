from seo.rules import (
    make_issue,
    check_title,
    check_meta_description,
    check_headings,
    check_images,
    check_links,
    check_redirects,
    check_canonicalization,
    check_duplicate_content,
    check_internationalization,
)


CHECKS = [
    check_title,
    check_meta_description,
    check_headings,
    check_images,
    check_links,
    check_redirects,
    check_canonicalization,
    check_duplicate_content,
    check_internationalization,
]


def check_seo(soup):
    """her sorun make_issue sözlüğüdür"""
    if soup is None:
        return [make_issue("PAGE_CONTENT_MISSING", "technical", "critical",
                           "Sayfa içeriği alınamadı.")]
    issues = []
    for check in CHECKS:
        issues.extend(check(soup))
    return issues