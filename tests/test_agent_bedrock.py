"""Bedrock agent mode, with a fake client (no AWS calls in tests)."""

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from on_time import agent as agent_module
from on_time.agent import BedrockAgent, next_step

TOOLS = [
    {"name": "set_light", "description": "Turn a light on or off", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_schedule", "description": "Schedule", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "set_demo_clock", "description": "Demo only", "inputSchema": {"type": "object", "properties": {}}},
]


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def agent_with(client) -> BedrockAgent:
    agent = BedrockAgent.__new__(BedrockAgent)
    agent.model_id, agent.max_tokens, agent.client = "test-model", 400, client
    return agent


def tool_use(name, input_, id_="tu1"):
    return {"toolUse": {"toolUseId": id_, "name": name, "input": input_}}


def test_tool_use_becomes_tool_calls_and_request_is_well_formed():
    client = FakeClient({"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
        {"text": "Turning it on."}, tool_use("set_light", {"light_id": "porch", "action": "on"})]}}})  # fmt: skip
    step = next_step([{"role": "user", "text": "porch on"}], TOOLS, mode="bedrock", bedrock=agent_with(client))
    assert step == {
        "type": "tool_calls",
        "calls": [{"id": "tu1", "name": "set_light", "arguments": {"light_id": "porch", "action": "on"}}],
        "text": "Turning it on.",
        "mode": "bedrock",
    }
    request = client.calls[0]
    assert request["inferenceConfig"]["maxTokens"] == 400  # always explicit
    assert [t["toolSpec"]["name"] for t in request["toolConfig"]["tools"]] == ["set_light", "get_schedule"]
    assert request["system"][0]["text"].startswith("You are Alexa+ with the On Time add-on")


def test_end_turn_is_a_reply():
    client = FakeClient({"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": "Done."}]}}})
    step = next_step([{"role": "user", "text": "hi"}], TOOLS, mode="bedrock", bedrock=agent_with(client))
    assert step == {"type": "reply", "text": "Done.", "mode": "bedrock"}


def test_tools_the_page_did_not_offer_are_dropped():
    client = FakeClient({"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
        tool_use("set_demo_clock", {"reset": True}), tool_use("delete_everything", {})]}}})  # fmt: skip
    step = next_step([{"role": "user", "text": "x"}], TOOLS, mode="bedrock", bedrock=agent_with(client))
    assert step["type"] == "reply"


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse"),
        NoCredentialsError(),
    ],
)
def test_bedrock_failure_falls_back_to_mock_and_cools_down(error):
    failing = FakeClient(error=error)
    step = next_step([{"role": "user", "text": "hello"}], TOOLS, mode="bedrock", bedrock=agent_with(failing))
    assert step["mode"] == "mock"
    assert step["text"].startswith("Hi!")
    assert step["fallback_reason"] in ("ClientError", "NoCredentialsError")

    # Within the cool-down, Bedrock isn't retried on every step.
    next_step([{"role": "user", "text": "hello"}], TOOLS, mode="bedrock", bedrock=agent_with(failing))
    assert len(failing.calls) == 1


@pytest.fixture(autouse=True)
def reset_cooldown():
    agent_module._bedrock_down = None
    yield
    agent_module._bedrock_down = None


def test_window_that_opens_mid_turn_starts_at_the_users_words():
    """The page sends only the latest messages; a window opening on a tool result must not reach Converse
    (a toolResult without its toolUse is a ValidationException, seen in live testing on Oct 13)."""
    messages = [
        {"role": "tool", "results": [{"id": "c0", "name": "list_routines", "ok": True, "data": {}, "text": ""}]},
        {"role": "assistant", "text": "You have four routines."},
        {"role": "user", "text": "When do my lights come on on Thanksgiving?"},
    ]
    converted = BedrockAgent.converse_messages(messages)
    assert converted == [{"role": "user", "content": [{"text": "When do my lights come on on Thanksgiving?"}]}]


def test_system_prompt_guards_routine_removal_and_dates():
    assert "remove or change a routine the user names" in agent_module.SYSTEM_PROMPT
    assert "call get_schedule with that date" in agent_module.SYSTEM_PROMPT


def test_conversation_maps_to_alternating_converse_messages():
    messages = [
        {"role": "assistant", "text": "Hello!"},  # leading assistant turns are dropped
        {"role": "user", "text": "Turn on the porch"},
        {"role": "assistant", "text": "", "tool_calls": [{"id": "c1", "name": "set_light", "arguments": {"light_id": "porch", "action": "on"}}]},
        {"role": "tool", "results": [{"id": "c1", "name": "set_light", "ok": True, "data": {"light": {"is_on": True}}, "text": ""}]},
        {"role": "assistant", "text": "", "tool_calls": [{"id": "c2", "name": "get_schedule", "arguments": {}}]},
        {"role": "tool", "results": [{"id": "c2", "name": "get_schedule", "ok": False, "data": None, "text": "Set the home location first"}]},
        {"role": "assistant", "text": "Done.", "context": {"checkin": {"id": "ci_1"}}},
        {"role": "user", "text": "yes"},
    ]
    converted = BedrockAgent.converse_messages(messages)
    assert [m["role"] for m in converted] == ["user", "assistant", "user", "assistant", "user", "assistant", "user"]
    assert converted[1]["content"][0]["toolUse"]["toolUseId"] == "c1"
    assert converted[2]["content"][0]["toolResult"]["content"] == [{"json": {"light": {"is_on": True}}}]
    error_result = converted[4]["content"][0]["toolResult"]
    assert error_result["status"] == "error" and error_result["content"] == [{"text": "Set the home location first"}]
    assert converted[6]["content"] == [{"text": "yes"}]
