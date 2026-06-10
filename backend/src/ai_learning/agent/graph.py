from ai_learning.agent.tools import choose_tool


def run_agent(task: str) -> str:
    tool = choose_tool(task)
    observation = tool(task)
    return f"Task: {task}\nObservation: {observation}"
