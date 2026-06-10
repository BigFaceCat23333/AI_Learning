from ai_learning.agent.graph import run_agent
from ai_learning.agent.tools import choose_tool, summarize_tool


def test_choose_tool_uses_summary_tool() -> None:
    assert choose_tool("please summarize this") is summarize_tool


def test_run_agent_returns_observation() -> None:
    result = run_agent("hello")
    assert "Task: hello" in result
    assert "Observation: hello" in result
