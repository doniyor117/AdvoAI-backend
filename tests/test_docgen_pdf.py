from app.services.docgen import render_contract_pdf


def test_render_contract_pdf_produces_valid_pdf(tmp_path):
    contract = {
        "title": "Xizmat shartnomasi",
        "intro": "Ushbu shartnoma taraflar oʻrtasida tuzildi.",
        "sections": [
            {"number": 1, "heading": "Predmet", "body": "Birinchi band matni."},
        ],
        "signature_blocks": [
            {"party_role": "Buyurtmachi", "party_name": "A"},
            {"party_role": "Ijrochi", "party_name": "B"},
        ],
    }

    path = render_contract_pdf(contract, output_dir=str(tmp_path))

    with open(path, "rb") as f:
        header = f.read(5)
    assert header.startswith(b"%PDF-")
