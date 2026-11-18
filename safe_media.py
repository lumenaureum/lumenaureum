from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIL_FORMATS = ("JPEG", "PNG", "GIF", "ICO")
PIL_MAX_PIXELS = 89_478_485
PDF_FILES = (ROOT / "static", ROOT / "brand")

def restrict_pillow():

    from PIL import Image
    Image.init()
    for fmt in list(Image.OPEN):
        if fmt not in PIL_FORMATS:
            del Image.OPEN[fmt]
    Image.ID[:] = [fmt for fmt in Image.ID if fmt in PIL_FORMATS]
    Image.MAX_IMAGE_PIXELS = PIL_MAX_PIXELS
    return tuple(Image.ID)

def pdf_fetcher():

    from urllib.parse import unquote, urlparse
    from weasyprint.urls import URLFetcher

    class LocalOnly(URLFetcher):
        def __init__(self):
            super().__init__(allowed_protocols=("data", "file"), allow_redirects=False)

        def fetch(self, url, headers=None):
            if url[:5].lower() == "file:":
                p = Path(unquote(urlparse(url).path)).resolve()
                if not any(p.is_relative_to(d) for d in PDF_FILES):
                    raise ValueError("a document may use only the app's own files")
            elif url[:5].lower() != "data:":
                raise ValueError("a document fetches nothing from the network")
            return super().fetch(url, headers)

    return LocalOnly()
