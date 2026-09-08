from fastapi.testclient import TestClient

from offerpilot.api import create_app
from offerpilot.config import Config, SkillPackage, load_config, save_config


def test_skills_api_registers_untrusted_package(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/api/skills",
        json={
            "id": "resume-coach",
            "label": "Resume Coach",
            "version": "0.1.0",
            "source": "file:///skills/resume-coach",
        },
    )

    assert response.status_code == 201
    assert response.json()["packages"] == [
        {
            "id": "resume-coach",
            "label": "Resume Coach",
            "version": "0.1.0",
            "description": "",
            "source": "file:///skills/resume-coach",
            "source_type": "local",
            "entrypoint": "",
            "manifest_digest": "",
            "trusted": False,
            "enabled": False,
            "loaded": False,
        }
    ]
    assert response.json()["loaded"] == []


def test_skills_api_registers_manifest_with_provenance(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.post(
        "/api/skills",
        json={
            "source": "file:///skills/resume-coach",
            "manifest": {
                "id": "resume-coach",
                "label": "Resume Coach",
                "version": "0.1.0",
                "description": "Resume review assistant",
                "entrypoint": "SKILL.md",
            },
        },
    )

    assert response.status_code == 201
    package = response.json()["packages"][0]
    assert package["id"] == "resume-coach"
    assert package["description"] == "Resume review assistant"
    assert package["entrypoint"] == "SKILL.md"
    assert package["source_type"] == "local"
    assert len(package["manifest_digest"]) == 64
    assert package["trusted"] is False
    assert package["enabled"] is False


def test_skills_api_rejects_invalid_manifest(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))

    missing_id = client.post(
        "/api/skills",
        json={"source": "file:///skills/bad", "manifest": {"label": "Bad"}},
    )
    mismatch = client.post(
        "/api/skills",
        json={
            "id": "resume-coach",
            "source": "file:///skills/resume-coach",
            "manifest": {"id": "other", "label": "Other"},
        },
    )

    assert missing_id.status_code == 400
    assert missing_id.json() == {"error": "manifest.id is required"}
    assert mismatch.status_code == 400
    assert mismatch.json() == {"error": "manifest id must match id"}


def test_skills_api_requires_trust_before_enable(tmp_path):
    save_config(
        tmp_path,
        Config(
            skills=[
                SkillPackage(
                    id="resume-coach",
                    label="Resume Coach",
                    source="file:///skills/resume-coach",
                    trusted=False,
                    enabled=False,
                )
            ]
        ),
    )
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.put("/api/skills/resume-coach", json={"enabled": True})

    assert response.status_code == 400
    assert response.json()["error"] == "skill must be trusted before enabling"


def test_skills_api_loads_only_trusted_enabled_packages(tmp_path):
    save_config(
        tmp_path,
        Config(
            skills=[
                SkillPackage(
                    id="resume-coach",
                    label="Resume Coach",
                    source="file:///skills/resume-coach",
                    trusted=False,
                    enabled=False,
                )
            ]
        ),
    )
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.put("/api/skills/resume-coach", json={"trusted": True, "enabled": True})

    assert response.status_code == 200
    assert response.json()["loaded"] == ["resume-coach"]
    assert response.json()["packages"][0]["loaded"] is True

    cfg = load_config(tmp_path)
    assert cfg.skills[0].trusted is True
    assert cfg.skills[0].enabled is True
