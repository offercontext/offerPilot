import json

from fastapi.testclient import TestClient

from offerpilot.ai.types import Assistant
from offerpilot.api import create_app


class JSONModel:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        return Assistant(content=json.dumps(self.payload, ensure_ascii=False))


def test_notes_to_practice_generation_review_workflow(tmp_path):
    model = JSONModel(
        {
            "questions": [
                {
                    "category": "系统设计",
                    "difficulty": "hard",
                    "question": "如何设计一个高并发短链系统？",
                    "reference_answer": "发号器、缓存、限流、异步写入。",
                    "tags": ["缓存", "限流"],
                }
            ]
        }
    )
    client = TestClient(create_app(data_dir=tmp_path, chat_model=model))

    application = client.post(
        "/api/applications",
        json={"company_name": "牛客网", "position_name": "后端", "status": "interview"},
    ).json()
    client.post(
        f"/api/applications/{application['id']}/notes",
        json={
            "company": "牛客网",
            "position": "后端",
            "round": "技术一面",
            "date": "2026-07-11",
            "questions": "短链系统需要发号器、缓存、限流和异步写入。",
        },
    )
    generated = client.post(
        "/api/questions/generate",
        json={
            "source": "notes",
            "topic": "system-design",
            "count": 1,
            "application_id": application["id"],
        },
    )

    assert generated.status_code == 201
    question = generated.json()["questions"][0]
    assert question["question"] == "如何设计一个高并发短链系统？"
    assert question["source_type"] == "ai_notes"
    assert client.get("/api/questions/due").json()[0]["id"] == question["id"]

    reviewed = client.post(f"/api/questions/{question['id']}/reviews", json={"rating": 3})

    assert reviewed.status_code == 201
    stats = client.get("/api/questions/stats").json()
    assert stats["total"] == 1
    assert stats["mastered"] == 1
    assert stats["due"] == 0
    assert stats["today_reviews"] == 1
