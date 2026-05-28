"""Classifier tests with a fake Anthropic client — no network, no API key."""

from types import SimpleNamespace

from email2data.classifier import classify

PLAYBOOK = "test playbook"
SETTINGS = {"claude": {"model": "x", "max_tokens": 256, "ignore_confidence_floor": 0.85}}
ENV = {"message_id": "mid:x@y", "subject": "s", "from": {"email": "a@b.pt"}, "attachments": []}


class FakeClient:
    """Returns a canned tool_use block, capturing the create() kwargs for assertions."""

    def __init__(self, tool_input):
        self._input = tool_input
        self.kwargs = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.kwargs = kwargs
        block = SimpleNamespace(type="tool_use", name="record_triage", input=self._input)
        return SimpleNamespace(content=[block])


def _verdict(**over):
    base = {"type": "CLIENT_JOB_REQUEST", "priority": "HIGH", "urgency": 80,
            "confidence": 0.9, "reason": "r", "entities": {}}
    base.update(over)
    return base


def test_happy_path_maps_fields():
    r = classify(ENV, PLAYBOOK, FakeClient(_verdict()), SETTINGS)
    assert r.type == "CLIENT_JOB_REQUEST" and r.priority == "HIGH" and r.urgency == 80
    assert r.message_id == "mid:x@y" and r.subject == "s" and r.from_addr == "a@b.pt"


def test_incoherent_ignore_on_client_is_downgraded():
    # The costly bug: a real client marked IGNORE, even with high confidence -> must become review.
    r = classify(ENV, PLAYBOOK, FakeClient(_verdict(priority="IGNORE", confidence=0.99)), SETTINGS)
    assert r.priority == "NEEDS_REVIEW"
    assert "guardrail" in r.reason


def test_low_confidence_ignore_on_publicity_is_downgraded():
    r = classify(ENV, PLAYBOOK, FakeClient(_verdict(type="PUBLICITY", priority="IGNORE", confidence=0.5)), SETTINGS)
    assert r.priority == "NEEDS_REVIEW"


def test_confident_publicity_ignore_is_kept():
    r = classify(ENV, PLAYBOOK, FakeClient(_verdict(type="PUBLICITY", priority="IGNORE", urgency=5, confidence=0.95)), SETTINGS)
    assert r.priority == "IGNORE"


def test_out_of_range_values_clamped_and_enums_validated():
    r = classify(ENV, PLAYBOOK, FakeClient(_verdict(type="BOGUS", priority="??", urgency=999, confidence=5)), SETTINGS)
    assert r.type == "OTHER" and r.priority == "NEEDS_REVIEW"
    assert r.urgency == 100 and r.confidence == 1.0


def test_forced_tool_choice_and_playbook_cached():
    fc = FakeClient(_verdict())
    classify(ENV, PLAYBOOK, fc, SETTINGS)
    assert fc.kwargs["tool_choice"] == {"type": "tool", "name": "record_triage"}
    assert fc.kwargs["temperature"] == 0
    assert fc.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
