import math
import re
from collections import Counter


Vector = dict[str, float]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in re.finditer(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        value = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", value):
            tokens.extend(value[index : index + 2] for index in range(max(len(value) - 1, 1)))
        else:
            tokens.append(value)
    return [token for token in tokens if token.strip()]


def embed(text: str) -> Vector:
    counts = Counter(tokenize(text))
    norm = math.sqrt(sum(value * value for value in counts.values()))
    if norm == 0:
        return {}
    return {token: count / norm for token, count in counts.items()}


def cosine_similarity(left: Vector, right: Vector) -> float:
    if not left or not right:
        return 0.0
    return sum(left.get(token, 0.0) * right.get(token, 0.0) for token in left)
