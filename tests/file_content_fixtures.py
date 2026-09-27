"""Generated inert documents only; no coursework, credentials or remote addresses."""

import io
import zipfile
import zlib

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    ArrayObject,
    NumberObject,
    TextStringObject,
)

from canvas_mcp.infrastructure.files.content_parser import W, A, P, R, REL, CT


def image_bytes(fmt="PNG", text="Write a report about finite automata."):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1000, 240), "white")
    ImageDraw.Draw(image).text((30, 60), text, font=ImageFont.load_default(size=32), fill="black")
    output = io.BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


def scanned_pdf_bytes(*, pages=1, hybrid=False):
    from PIL import Image
    from pypdf import PdfReader

    writer = PdfWriter()
    if hybrid:
        writer.add_page(
            PdfReader(
                io.BytesIO(
                    pdf_bytes(
                        ("Native requirement: " + "Explain automata and language closure. " * 4,)
                    )
                )
            ).pages[0]
        )
    for _ in range(pages):
        image = Image.open(io.BytesIO(image_bytes()))
        page = writer.add_blank_page(width=600, height=144)
        raster = DecodedStreamObject()
        raster.set_data(zlib.compress(image.tobytes()))
        raster.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(image.width),
                NameObject("/Height"): NumberObject(image.height),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/Filter"): NameObject("/FlateDecode"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/XObject"): DictionaryObject(
                    {NameObject("/Im0"): writer._add_object(raster)}
                )
            }
        )
        content = DecodedStreamObject()
        content.set_data(b"q 600 0 0 144 0 0 cm /Im0 Do Q")
        page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def pdf_bytes(texts=("Controlled PDF requirement: write a short report.",), *, active=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(("BT /F1 12 Tf 72 720 Td (" + escaped + ") Tj ET").encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if active:
        writer.add_js("throw new Error('MUST NEVER EXECUTE');")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def package_bytes(parts, *, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return output.getvalue()


def unicode_pdf_bytes():
    def d(**items):
        return DictionaryObject({NameObject("/" + key): value for key, value in items.items()})

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    cmap = DecodedStreamObject()
    cmap.set_data(
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        b"1 beginbfchar\n<0001> <0416>\nendbfchar\nendcmap\n"
        b"CMapName currentdict /CMap defineresource pop\nend\nend"
    )
    descendant = d(
        Type=NameObject("/Font"),
        Subtype=NameObject("/CIDFontType2"),
        BaseFont=NameObject("/IdentityFont"),
        DW=NumberObject(600),
        CIDSystemInfo=d(
            Registry=TextStringObject("Adobe"),
            Ordering=TextStringObject("Identity"),
            Supplement=NumberObject(0),
        ),
    )
    font = d(
        Type=NameObject("/Font"),
        Subtype=NameObject("/Type0"),
        BaseFont=NameObject("/IdentityFont"),
        Encoding=NameObject("/Identity-H"),
        ToUnicode=writer._add_object(cmap),
        DescendantFonts=ArrayObject([writer._add_object(descendant)]),
    )
    page[NameObject("/Resources")] = d(Font=d(F1=writer._add_object(font)))
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td <0001> Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def content_types(fmt):
    main = "/word/document.xml" if fmt == "docx" else "/ppt/presentation.xml"
    kind = "wordprocessingml.document" if fmt == "docx" else "presentationml.presentation"
    return f'<Types xmlns="{CT[1:-1]}"><Override PartName="{main}" ContentType="application/vnd.openxmlformats-officedocument.{kind}.main+xml"/></Types>'


def docx_parts(body=None):
    if body is None:
        body = '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Report heading</w:t></w:r></w:p><w:p><w:hyperlink r:id="external"><w:r><w:t>External label</w:t><w:tab/><w:t>is inert</w:t></w:r></w:hyperlink></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Deliverable</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Two pages</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>After the table</w:t></w:r></w:p>'
    return {
        "[Content_Types].xml": content_types("docx"),
        "word/document.xml": f'<w:document xmlns:w="{W[1:-1]}" xmlns:r="{R[1:-1]}"><w:body>{body}</w:body></w:document>',
        "word/_rels/document.xml.rels": f'<Relationships xmlns="{REL[1:-1]}"><Relationship Id="external" Type="{R[1:-1]}/hyperlink" Target="https://must-never-fetch.example/secret" TargetMode="External"/></Relationships>',
    }


def pptx_parts():
    parts = {
        "[Content_Types].xml": content_types("pptx"),
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{P[1:-1]}" xmlns:r="{R[1:-1]}"><p:sldIdLst><p:sldId id="256" r:id="second"/><p:sldId id="257" r:id="first"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{REL[1:-1]}"><Relationship Id="first" Type="{R[1:-1]}/slide" Target="slides/slide1.xml"/><Relationship Id="second" Type="{R[1:-1]}/slide" Target="slides/slide2.xml"/></Relationships>',
        "ppt/slides/_rels/slide2.xml.rels": f'<Relationships xmlns="{REL[1:-1]}"><Relationship Id="notes" Type="{R[1:-1]}/notesSlide" Target="../notesSlides/notesSlide9.xml"/><Relationship Id="web" Type="{R[1:-1]}/hyperlink" Target="https://never-fetch.example/" TargetMode="External"/></Relationships>',
        "ppt/notesSlides/notesSlide9.xml": f'<p:notes xmlns:p="{P[1:-1]}" xmlns:a="{A[1:-1]}"><a:p><a:r><a:t>Speaker requirement</a:t></a:r></a:p></p:notes>',
    }
    for number, text in ((1, "Presented second"), (2, "Presented first")):
        parts[f"ppt/slides/slide{number}.xml"] = (
            f'<p:sld xmlns:p="{P[1:-1]}" xmlns:a="{A[1:-1]}"><p:cSld><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:cSld></p:sld>'
        )
    return parts
