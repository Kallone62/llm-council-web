"""Regression: failed conversation writes and corrupt neighbours cannot lose data."""
import json
import uuid
from pathlib import Path

import pytest

from backend import storage


@pytest.fixture
def directory(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    return tmp_path


def new_conversation():
    return storage.create_conversation(str(uuid.uuid4()))


def assert_unchanged(directory, path, before):
    assert path.read_bytes() == before
    assert list(directory.iterdir()) == [path]


def test_serialization_failure_preserves_existing_conversation(directory):
    conversation = new_conversation()
    storage.add_user_message(conversation["id"], "Türkçe ✓")
    path = directory / f'{conversation["id"]}.json'
    before = path.read_bytes()
    updated = storage.get_conversation(conversation["id"])
    updated["unserializable"] = object()
    with pytest.raises(TypeError):
        storage.save_conversation(updated)
    assert_unchanged(directory, path, before)
    assert storage.get_conversation(conversation["id"])["messages"][0]["content"] == "Türkçe ✓"


@pytest.mark.parametrize("operation", ["user", "assistant", "title", "memory"])
def test_failed_replace_preserves_existing_conversation(directory, monkeypatch, operation):
    conversation = new_conversation()
    cid = conversation["id"]
    storage.add_user_message(cid, "Original")
    path = directory / f"{cid}.json"
    before = path.read_bytes()
    attempts = []

    def fail_replace(source, target):
        source, target = Path(source), Path(target)
        attempts.append(source)
        assert source.parent == target.parent == directory
        assert source != target
        assert target.read_bytes() == before
        assert json.loads(source.read_text(encoding="utf-8"))["id"] == cid
        raise OSError("Injected replacement failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    operations = {
        "user": lambda: storage.add_user_message(cid, "Next"),
        "assistant": lambda: storage.add_assistant_message(cid, [], [], {"response": "Final"}),
        "title": lambda: storage.update_conversation_title(cid, "Updated"),
        "memory": lambda: storage.update_conversation_memory(cid, {"summary": "Memory"}),
    }
    with pytest.raises(OSError, match="Injected replacement failure"):
        operations[operation]()
    assert len(attempts) == 1
    assert_unchanged(directory, path, before)


def test_failed_create_does_not_leave_partial_file(directory, monkeypatch):
    def fail_dump(_data, handle, **_kwargs):
        handle.write('{"partial":')
        raise OSError("Injected write failure")

    monkeypatch.setattr(storage.json, "dump", fail_dump)
    with pytest.raises(OSError, match="Injected write failure"):
        new_conversation()
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("bad", [b'{"broken":', b'\xff', b'[]', b'{}',
                                 b'{"id":"wrong","created_at":"x","messages":[]}',
                                 b'{"id":"BAD_ID","created_at":42,"messages":[]}',
                                 b'{"id":"BAD_ID","created_at":"x","messages":null}'])
def test_corrupt_neighbour_is_logged_and_preserved(directory, caplog, bad):
    first, second = new_conversation(), new_conversation()
    storage.add_user_message(first["id"], "Preserve this")
    bad_id = str(uuid.uuid4())
    bad = bad.replace(b"BAD_ID", bad_id.encode())
    path = directory / f"{bad_id}.json"
    path.write_bytes(bad)
    result = storage.list_conversations()
    assert [row["id"] for row in result] == [second["id"], first["id"]]
    assert result[1]["message_count"] == 1
    assert path.read_bytes() == bad
    assert path.name in caplog.text
    assert storage.get_conversation(first["id"])["messages"][0]["content"] == "Preserve this"


def test_successful_write_preserves_full_round_and_legacy_default_title(directory):
    conversation = new_conversation()
    cid = conversation["id"]
    storage.add_user_message(cid, "Soru ✓")
    storage.add_assistant_message(cid, [{"response": "A"}], [{"ranking": "B"}],
                                  {"response": "Yanıt"}, {"status": "partial"})
    storage.update_conversation_memory(cid, {"summary": "Özet"})
    saved = storage.get_conversation(cid)
    del saved["title"]
    storage.save_conversation(saved)
    assert storage.get_conversation(cid) == saved
    assert storage.list_conversations()[0]["title"] == "New Conversation"
    assert storage.list_conversations()[0]["message_count"] == 2
    assert len(list(directory.iterdir())) == 1
