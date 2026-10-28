"""Собирает docs/agent-guide.md из docs/agent-guide.src.md: подставляет ссылки на код с номерами строк.

В исходнике ссылки пишутся как {{core.py}} или {{core.py:TravelAgent._turn}}. После правок кода
номера строк сдвигаются, поэтому руководство пересобирается: python3 scripts/agent_guide_links.py
"""

import ast
import re
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
src = repo / "docs/agent-guide.src.md"
out = repo / "docs/agent-guide.md"
app = repo / "backend/app"


def locate(name):
    for base in (app / "agent", app):
        if (base / name).exists():
            return base / name
    raise SystemExit(f"нет файла {name}")


def symbol_range(path, symbol):
    tree = ast.parse(path.read_text())
    parts = symbol.split(".")
    nodes = tree.body
    for i, part in enumerate(parts):
        for node in nodes:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            if part in names:
                if i == len(parts) - 1:
                    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
                    return start, node.end_lineno
                nodes = node.body
                break
        else:
            raise SystemExit(f"нет символа {symbol} в {path}")


def link(m):
    ref = m.group(1)
    if ref == "dir":
        return "[backend/app/agent/](../backend/app/agent/)"
    name, _, symbol = ref.partition(":")
    path = locate(name)
    rel = "../" + str(path.relative_to(repo))
    short = path.name
    if not symbol:
        return f"[`{short}`]({rel})"
    a, b = symbol_range(path, symbol)
    anchor = f"#L{a}" if a == b else f"#L{a}-L{b}"
    return f"[`{short}` → `{symbol}`]({rel}{anchor})"


text = re.sub(r"\{\{([^}]+)\}\}", link, src.read_text())
out.write_text(text)
count = len(re.findall(r"\{\{", src.read_text()))
print(f"{out.relative_to(repo)}: ссылок {count}")
