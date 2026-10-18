from seo.rules import (
    make_issue,
    check_title,
    check_meta_description,
    check_headings,
    check_images,
    check_links,
    check_canonical,
    check_open_graph,
    check_twitter_cards,
    check_schema_markup,
)
from seo.site_rules import check_site, check_duplicates 

CHECKS = [
    check_title,
    check_meta_description,
    check_headings,
    check_images,
    check_links,
    check_canonical,
    check_open_graph,
    check_twitter_cards,
    check_schema_markup,
]


def check_seo(soup):
    
    if soup is None:
        return [make_issue("PAGE_CONTENT_MISSING", "technical", "Sayfa içeriği alınamadı.")]
    issues = []
    for check in CHECKS:
        issues.extend(check(soup))
    return issues