import pytest

# A made-up two-page paper: page breaks are form feeds, as pdftotext writes them.
PAGES = [
    "Learned calibration of ensemble forecasts at stations\nA. Moreau and S. Ito\n"
    "Abstract. We calibrate ensemble forecasts at 1,234 stations over two years.\n",
    "Table 4: Mean CRPS by method.\nRaw ensemble 1.42\nSite calibration 0.70\n"
    "With station embed-\ndings the network reaches a mean CRPS of 0.63 on the test year,\n"
    "an improvement of −0.07 over site calibration.\n",
]


@pytest.fixture
def paper(tmp_path):
    path = tmp_path / "paper.txt"
    path.write_text("\f".join(PAGES) + "\f", encoding="utf-8")
    return path


def tiny_pdf(lines: list) -> bytes:
    """A one-page PDF showing `lines` in Helvetica, built by hand (no PDF library needed)."""
    text = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({s}) Tj T*" for s in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out
