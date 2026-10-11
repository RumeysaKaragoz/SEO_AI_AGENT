from seo.crawler import crawl
from seo.rules import check_links, check_title, check_meta_description, check_headings, check_images, check_links,first_check, check_redirects, check_canonicalization, check_duplicate_content, check_internationalization
def check_seo(soup):
    issues = []
    issues.extend(first_check(soup))
    issues.extend(check_title(soup))
    issues.extend(check_meta_description(soup))
    issues.extend(check_headings(soup))
    issues.extend(check_images(soup))
    issues.extend(check_links(soup))
    issues.extend(check_redirects(soup))
    issues.extend(check_canonicalization(soup))
    issues.extend(check_duplicate_content(soup))
    issues.extend(check_internationalization(soup))
    return issues
                