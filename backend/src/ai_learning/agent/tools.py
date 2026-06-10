from collections.abc import Callable


Tool = Callable[[str], str]


def echo_tool(task: str) -> str:
    return task


def summarize_tool(task: str) -> str:
    words = task.split()
    return " ".join(words[:20])


def choose_tool(task: str) -> Tool:
    if "summary" in task.lower() or "summarize" in task.lower():
        return summarize_tool
    return echo_tool
