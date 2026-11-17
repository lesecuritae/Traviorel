"""Resource-bounded PDF text subprocess; receives bytes, never filenames/URLs."""
import io
import json
import sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(192*1024*1024,192*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(5,5))
    from pypdf import PdfReader
    data=sys.stdin.buffer.read(2_000_001)
    if len(data)>2_000_000 or not data.startswith(b'%PDF-'): raise ValueError('invalid PDF input')
    reader=PdfReader(io.BytesIO(data),strict=True)
    if reader.is_encrypted or len(reader.pages)>24: raise ValueError('unsupported PDF size/encryption')
    pages=[];size=0
    for page in reader.pages:
        content=page.get_contents()
        if content and len(content.get_data())>8_000_000: raise ValueError('large decoded content')
        text=page.extract_text(extraction_mode='layout',layout_mode_space_vertically=False)
        size+=len(text)
        if size>300000: raise ValueError('large PDF text')
        pages.append(text)
    print(json.dumps(pages,ensure_ascii=False))


if __name__=='__main__':main()
