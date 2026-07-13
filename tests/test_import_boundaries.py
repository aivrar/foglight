import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _python_dependencies(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dependencies = set()
    module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dependencies.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                dependencies.add(".".join(filter(None, (base, node.module))))
            else:
                dependencies.add(node.module)
    return dependencies


def _assert_acyclic(graph):
    visiting = set()
    visited = set()

    def visit(node, trail):
        if node in visiting:
            raise AssertionError("circular dependency: " + " -> ".join((*trail, node)))
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, ()):
            if dependency in graph:
                visit(dependency, (*trail, node))
        visiting.remove(node)
        visited.add(node)

    for module in graph:
        visit(module, ())


def test_python_core_never_imports_server_native_or_desktop_layers_and_has_no_cycles():
    graph = {}
    for path in (ROOT / "foglight_core").rglob("*.py"):
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        dependencies = _python_dependencies(path)
        assert not any(
            dependency.startswith(("foglight_server", "foglight_native", "webview"))
            for dependency in dependencies
        ), f"{module} crossed into an application lifecycle layer"
        graph[module] = {dependency for dependency in dependencies if dependency.startswith("foglight_core")}
    _assert_acyclic(graph)


def test_browser_core_modules_do_not_import_app_and_have_no_cycles():
    module_paths = {
        path.stem: path
        for path in (ROOT / "web").glob("*.js")
        if path.name != "app.js"
    }
    graph = {}
    for name, path in module_paths.items():
        source = path.read_text(encoding="utf-8")
        imports = set(re.findall(r"from\s+['\"]\./([^'\"]+)\.js['\"]", source))
        assert "app" not in imports, f"{name}.js imports the application entrypoint"
        graph[name] = imports
    _assert_acyclic(graph)
