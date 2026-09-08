from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.db import init_database
from offerpilot.repositories.resumes import ResumeMatchCreate, ResumesRepository


def _pdf_with_text(text: str) -> bytes:
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/MediaBox [0 0 612 792] /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    body = b"%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(body)
    xref = b"0000000000 65535 f \n" + b"".join(
        f"{offset:010d} 00000 n \n".encode() for offset in offsets
    )
    return (
        body
        + f"xref\n0 {len(objects) + 1}\n".encode()
        + xref
        + (
            f"trailer\n<< /Root 1 0 R /Size {len(objects) + 1} >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )


def test_create_manual_resume_returns_v01_structure_and_master_completion(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    created_response = client.post(
        "/api/resumes",
        json={
            "title": "Backend master resume",
            "source": "manual",
            "content_json": {
                "career_intent": {"target_roles": []},
                "contact": {"name": "Ada"},
                "education": [{"school": "XHS University"}],
                "experience": [{"company": "OfferPilot"}],
                "projects": [{"name": "Resume v0.1"}],
                "skills": ["Python", "FastAPI"],
            },
        },
    )

    assert created_response.status_code == 201
    created = created_response.json()
    assert created["title"] == "Backend master resume"
    assert created["name"] == "Backend master resume"
    assert created["is_master"] is True
    assert created["source"] == "manual"
    assert created["content_json"]["career_intent"] == {"target_roles": []}
    assert created["completion_percent"] < 100
    assert created["is_complete"] is False
    assert "career_intent" in created["missing_sections"]

    patched_response = client.patch(
        f"/api/resumes/{created['id']}",
        json={
            "content_json": {
                "career_intent": {"target_roles": ["Backend Engineer"]},
                "contact": {"name": "Ada"},
                "education": [{"school": "XHS University"}],
                "experience": [{"company": "OfferPilot"}],
                "projects": [{"name": "Resume v0.1"}],
                "skills": ["Python", "FastAPI"],
            }
        },
    )

    assert patched_response.status_code == 200
    patched = patched_response.json()
    assert patched["content_json"]["career_intent"]["target_roles"] == ["Backend Engineer"]
    assert patched["is_complete"] is True
    assert "career_intent" not in patched["missing_sections"]

    list_response = client.get("/api/resumes")
    assert list_response.status_code == 200
    assert [resume["id"] for resume in list_response.json()] == [created["id"]]


def test_upload_resume_accepts_only_pdf_and_keeps_compat_fields(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    docx_response = client.post(
        "/api/resumes/upload",
        files={"file": ("resume.docx", b"not a pdf", "application/octet-stream")},
    )

    assert docx_response.status_code == 400
    assert docx_response.json() == {"error": "only .pdf files are supported"}

    fake_pdf_response = client.post(
        "/api/resumes/upload",
        files={"file": ("fake.pdf", b"not a real pdf", "application/pdf")},
    )

    assert fake_pdf_response.status_code == 400
    assert fake_pdf_response.json() == {"error": "invalid PDF file"}
    assert client.get("/api/resumes").json() == []

    pdf_data = _pdf_with_text("Hello Resume Python")
    pdf_response = client.post(
        "/api/resumes/upload",
        files={"file": ("sample.pdf", pdf_data, "application/pdf")},
    )

    assert pdf_response.status_code == 201
    uploaded = pdf_response.json()
    assert uploaded["title"] == "sample"
    assert uploaded["name"] == "sample"
    assert uploaded["source"] == "upload"
    assert uploaded["source_file_path"] == f"resumes/{uploaded['id']}_sample.pdf"
    assert uploaded["file_path"] == uploaded["source_file_path"]
    assert uploaded["parse_status"] == "text-ready"
    assert "Hello Resume Python" in uploaded["parsed_data"]
    assert uploaded["content_json"]["raw_text"] == uploaded["parsed_data"]

    download = client.get(f"/api/resumes/{uploaded['id']}/file")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content == pdf_data


def test_create_resume_from_sample_returns_structured_sample(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post("/api/resumes/from-sample", json={"sample_id": "backend"})

    assert response.status_code == 201
    sample = response.json()
    assert sample["source"] == "sample"
    assert sample["title"]
    assert sample["is_master"] is True
    assert sample["content_json"]["career_intent"]["target_roles"]
    assert sample["content_json"]["experience"]
    assert sample["completion_percent"] > 0


def test_create_dialog_resume_uses_dialog_source_and_completion(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/api/resumes",
        json={
            "title": "Dialog built resume",
            "source": "dialog",
            "content_json": {
                "career_intent": {"target_roles": ["Backend Engineer"]},
                "contact": {"name": "Ada"},
                "education": [{"school": "XHS University"}],
                "experience": [{"company": "OfferPilot"}],
                "projects": [{"name": "Resume v0.1"}],
                "skills": ["Python"],
            },
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["source"] == "dialog"
    assert created["is_master"] is True
    assert created["is_complete"] is True
    assert created["completion_percent"] == 100


def test_patch_resume_updates_title_and_career_intent(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    created = client.post(
        "/api/resumes",
        json={
            "title": "Draft",
            "source": "manual",
            "content_json": {"career_intent": {"target_roles": []}},
        },
    ).json()

    response = client.patch(
        f"/api/resumes/{created['id']}",
        json={
            "title": "Master Backend Resume",
            "career_intent": {
                "target_roles": ["Backend Engineer"],
                "target_locations": ["Shanghai"],
            },
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "Master Backend Resume"
    assert updated["name"] == "Master Backend Resume"
    assert updated["content_json"]["career_intent"] == {
        "target_roles": ["Backend Engineer"],
        "target_locations": ["Shanghai"],
    }


def test_resume_raw_text_stays_in_compat_parsed_data(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    created = client.post(
        "/api/resumes",
        json={
            "title": "Structured",
            "source": "manual",
            "content_json": {"raw_text": "Raw text from structured editor"},
        },
    ).json()

    assert created["parsed_data"] == "Raw text from structured editor"
    assert created["content_json"]["raw_text"] == "Raw text from structured editor"

    response = client.patch(
        f"/api/resumes/{created['id']}",
        json={
            "content_json": {
                "career_intent": {"target_roles": ["Backend Engineer"]},
                "raw_text": "Updated raw text",
            }
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["parsed_data"] == "Updated raw text"
    assert updated["parse_status"] == "text-ready"
    assert updated["content_json"]["raw_text"] == "Updated raw text"


def test_patch_resume_preserves_single_master_invariant(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    master = client.post(
        "/api/resumes",
        json={
            "title": "Master",
            "source": "manual",
            "content_json": {"career_intent": {"target_roles": ["Backend Engineer"]}},
        },
    ).json()
    copy = client.post(f"/api/resumes/{master['id']}/copy", json={"title": "Variant"}).json()

    promoted_response = client.patch(f"/api/resumes/{copy['id']}", json={"is_master": True})

    assert promoted_response.status_code == 200
    assert promoted_response.json()["is_master"] is True
    assert client.get(f"/api/resumes/{master['id']}").json()["is_master"] is False

    rejected_response = client.patch(f"/api/resumes/{copy['id']}", json={"is_master": False})

    assert rejected_response.status_code == 400
    assert rejected_response.json() == {"error": "at least one master resume is required"}
    assert client.get(f"/api/resumes/{copy['id']}").json()["is_master"] is True


def test_copy_resume_creates_non_master_copy(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    original = client.post(
        "/api/resumes",
        json={
            "title": "Original",
            "source": "manual",
            "content_json": {"career_intent": {"target_roles": ["Backend Engineer"]}},
        },
    ).json()

    response = client.post(f"/api/resumes/{original['id']}/copy", json={"title": "Copy"})

    assert response.status_code == 201
    copied = response.json()
    assert copied["id"] != original["id"]
    assert copied["title"] == "Copy"
    assert copied["parent_resume_id"] == original["id"]
    assert copied["is_master"] is False
    assert copied["source"] in {"manual", "sample_copy"}
    assert copied["content_json"] == original["content_json"]


def test_deleted_resume_is_hidden_from_read_and_write_endpoints(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    master = client.post(
        "/api/resumes",
        json={"title": "Master", "source": "manual", "text": "Master raw text"},
    ).json()
    copy = client.post(f"/api/resumes/{master['id']}/copy", json={"title": "Variant"}).json()
    repo = ResumesRepository(init_database(tmp_path / "data.db"))
    repo.create_match(
        ResumeMatchCreate(
            resume_id=copy["id"],
            jd_text="Backend API engineer",
            result='{"summary":"legacy match"}',
        )
    )

    assert client.delete(f"/api/resumes/{copy['id']}").status_code == 200

    assert client.get(f"/api/resumes/{copy['id']}").status_code == 404
    assert client.patch(f"/api/resumes/{copy['id']}", json={"title": "Deleted"}).status_code == 404
    assert client.put(f"/api/resumes/{copy['id']}/text", json={"text": "new"}).status_code == 404
    assert client.get(f"/api/resumes/{copy['id']}/file").status_code == 404
    match_response = client.post(
        f"/api/resumes/{copy['id']}/match",
        json={"jd_text": "Backend API engineer"},
    )
    assert match_response.status_code == 404
    matches_response = client.get(f"/api/resumes/{copy['id']}/matches")
    assert matches_response.status_code == 404
    listed_ids = [resume["id"] for resume in client.get("/api/resumes").json()]
    assert copy["id"] not in listed_ids


def test_delete_resume_rejects_master_and_soft_deletes_non_master(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    master = client.post(
        "/api/resumes",
        json={
            "title": "Master",
            "source": "manual",
            "content_json": {"career_intent": {"target_roles": ["Backend Engineer"]}},
        },
    ).json()
    copy = client.post(f"/api/resumes/{master['id']}/copy", json={"title": "Variant"}).json()

    master_delete = client.delete(f"/api/resumes/{master['id']}")

    assert master_delete.status_code == 400
    assert master_delete.json() == {"error": "master resume cannot be deleted"}

    copy_delete = client.delete(f"/api/resumes/{copy['id']}")

    assert copy_delete.status_code == 200
    assert copy_delete.json() == {"message": "Deleted"}
    listed_ids = [resume["id"] for resume in client.get("/api/resumes").json()]
    assert master["id"] in listed_ids
    assert copy["id"] not in listed_ids


def test_delete_allows_empty_master_draft(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    draft = client.post(
        "/api/resumes",
        json={
            "title": "Pilot 对话薄版简历",
            "source": "dialog",
            "content_json": {
                "career_intent": {"target_roles": [], "target_locations": []},
                "contact": {},
                "education": [],
                "experience": [],
                "projects": [],
                "skills": [],
                "raw_text": "",
            },
        },
    ).json()

    assert draft["is_master"] is True
    assert draft["completion_percent"] == 0

    response = client.delete(f"/api/resumes/{draft['id']}")

    assert response.status_code == 200
    assert client.get("/api/resumes").json() == []
