from bs4 import BeautifulSoup

ALLOWED_ATTRS = {"href", "src", "alt", "title"}

def clean_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup([
        "script", "style", "iframe", "form",
        "noscript", "svg", "canvas", "input"
    ]):
        tag.decompose()

    for el in soup.find_all(True):
        el.attrs = {
            k: v for k, v in el.attrs.items()
            if k in ALLOWED_ATTRS
        }

    return str(soup)