from offerpilot.db import init_database
from offerpilot.repositories.chat import ChatRepository


def test_generated_title_cannot_overwrite_manual_rename(tmp_path):
    chat = ChatRepository(init_database(tmp_path / "data.db"))
    conversation = chat.create_conversation("首条消息")

    chat.update_conversation(
        conversation.id,
        {"title": "用户手动命名", "title_source": "manual"},
    )

    assert chat.apply_generated_title(conversation.id, "模型生成标题") is False
    current = chat.get_conversation(conversation.id)
    assert current is not None
    assert current.title == "用户手动命名"
    assert current.title_source == "manual"
