from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DML_METHODS = frozenset(
    {
        "abulk_create",
        "abulk_update",
        "acreate",
        "adelete",
        "aget_or_create",
        "asave",
        "aupdate",
        "aupdate_or_create",
        "bulk_create",
        "bulk_update",
        "create",
        "delete",
        "get_or_create",
        "save",
        "save_base",
        "update",
        "update_or_create",
    }
)
QUERYSET_METHODS = frozenset(
    {
        "alias",
        "all",
        "annotate",
        "defer",
        "distinct",
        "exclude",
        "filter",
        "none",
        "only",
        "order_by",
        "prefetch_related",
        "reverse",
        "select_for_update",
        "select_related",
        "using",
        "values",
        "values_list",
    }
)
INSTANCE_METHODS = frozenset({"acreate", "afirst", "aget", "alast", "create", "first", "get", "last"})
PAIR_INSTANCE_METHODS = frozenset({"aget_or_create", "aupdate_or_create", "get_or_create", "update_or_create"})


def _dotted_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        if prefix:
            return f"{prefix}.{node.attr}"
    return None


@dataclass(frozen=True, order=True)
class ModelDmlUse:
    method: str
    lineno: int
    receiver_kind: str


def _annotation_mentions_model(annotation: ast.expr | None, model_aliases: set[str], model_name: str) -> bool:
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in model_aliases:
            return True
        if isinstance(node, ast.Attribute) and node.attr == model_name:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if model_name in node.value or any(alias in node.value for alias in model_aliases):
                return True
    return False


class _ModelDmlVisitor(ast.NodeVisitor):
    def __init__(self, tree: ast.Module, *, model_name: str, relation_names: Iterable[str]) -> None:
        self.model_name = model_name
        self.relation_names = set(relation_names)
        self.model_aliases = {model_name}
        self.model_module_aliases: set[str] = set()
        self.uses: list[ModelDmlUse] = []
        self._env_stack: list[dict[str, str]] = []
        self._admin_model_stack: list[bool] = []
        self._collect_import_aliases(tree)
        self._return_helpers = self._collect_return_helpers(tree)
        self._proxy_helpers = self._collect_proxy_helpers(tree)

    @property
    def env(self) -> dict[str, str]:
        return self._env_stack[-1]

    def _collect_import_aliases(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == self.model_name:
                        self.model_aliases.add(alias.asname or alias.name)
                    if alias.name == "models" and (node.module or "").endswith("gameplay"):
                        self.model_module_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"gameplay.models", "gameplay.models.bots", "gameplay.models.arena_virtual"}:
                        self.model_module_aliases.add(alias.asname or alias.name)

    @staticmethod
    def _collect_proxy_helpers(tree: ast.Module) -> dict[str, dict[int, set[str]]]:
        helpers: dict[str, dict[int, set[str]]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parameter_indexes = {argument.arg: index for index, argument in enumerate(node.args.args)}
            parameter_methods: dict[int, set[str]] = {}
            for child in ast.walk(node):
                if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                    continue
                receiver = child.func.value
                if child.func.attr not in DML_METHODS or not isinstance(receiver, ast.Name):
                    continue
                index = parameter_indexes.get(receiver.id)
                if index is not None:
                    parameter_methods.setdefault(index, set()).add(child.func.attr)
            if parameter_methods:
                helpers[node.name] = parameter_methods
        return helpers

    def _static_return_kind(self, node: ast.AST | None, known_helpers: dict[str, str]) -> str:
        if isinstance(node, ast.Await):
            return self._static_return_kind(node.value, known_helpers)
        if isinstance(node, ast.Name):
            if node.id in self.model_aliases:
                return "model"
            return known_helpers.get(node.id, "unknown")
        if isinstance(node, ast.Attribute):
            if self._attribute_is_model(node):
                return "model"
            if node.attr == "objects" and self._static_return_kind(node.value, known_helpers) == "model":
                return "manager"
            if node.attr in self.relation_names:
                return "manager"
            return "unknown"
        if not isinstance(node, ast.Call):
            return "unknown"
        if isinstance(node.func, ast.Name):
            return known_helpers.get(node.func.id, "unknown")
        if not isinstance(node.func, ast.Attribute):
            return "unknown"
        receiver_kind = self._static_return_kind(node.func.value, known_helpers)
        if receiver_kind not in {"manager", "queryset"}:
            return "unknown"
        if node.func.attr in QUERYSET_METHODS:
            return "queryset"
        if node.func.attr in INSTANCE_METHODS:
            return "instance"
        if node.func.attr in PAIR_INSTANCE_METHODS:
            return "instance_pair"
        return "unknown"

    def _collect_return_helpers(self, tree: ast.Module) -> dict[str, str]:
        helpers: dict[str, str] = {}
        functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for _iteration in range(len(functions) + 1):
            changed = False
            for function in functions:
                return_kinds = {
                    self._static_return_kind(node.value, helpers)
                    for node in ast.walk(function)
                    if isinstance(node, ast.Return) and node.value is not None
                }
                known_kinds = return_kinds - {"unknown"}
                if len(known_kinds) == 1:
                    kind = next(iter(known_kinds))
                    if helpers.get(function.name) != kind:
                        helpers[function.name] = kind
                        changed = True
            if not changed:
                break
        return helpers

    def _attribute_is_model(self, node: ast.Attribute) -> bool:
        return node.attr == self.model_name and _dotted_name(node.value) in self.model_module_aliases

    def _expr_kind(self, node: ast.AST | None) -> str:
        if node is None:
            return "unknown"
        if isinstance(node, ast.Await):
            return self._expr_kind(node.value)
        if isinstance(node, ast.Name):
            if node.id in self.model_aliases:
                return "model"
            return self.env.get(node.id, "unknown")
        if isinstance(node, ast.Attribute):
            if self._attribute_is_model(node):
                return "model"
            if node.attr == "objects" and self._expr_kind(node.value) == "model":
                return "manager"
            if node.attr in self.relation_names:
                return "manager"
            return "unknown"
        if isinstance(node, ast.Subscript):
            return "instance" if self._expr_kind(node.value) in {"queryset", "instance_collection"} else "unknown"
        if isinstance(node, ast.IfExp):
            body_kind = self._expr_kind(node.body)
            return body_kind if body_kind == self._expr_kind(node.orelse) else "unknown"
        if not isinstance(node, ast.Call):
            return "unknown"

        if isinstance(node.func, ast.Name):
            if node.func.id == "list" and node.args and self._expr_kind(node.args[0]) in {"manager", "queryset"}:
                return "instance_collection"
            if node.func.id in self.model_aliases:
                return "instance"
            return self._return_helpers.get(node.func.id, "unknown")

        if not isinstance(node.func, ast.Attribute):
            return "unknown"
        receiver_kind = self._expr_kind(node.func.value)
        method = node.func.attr
        if receiver_kind in {"manager", "queryset"}:
            if method in QUERYSET_METHODS:
                return "queryset"
            if method in INSTANCE_METHODS:
                return "instance"
            if method in PAIR_INSTANCE_METHODS:
                return "instance_pair"
            if method in {"abulk_create", "bulk_create"}:
                return "instance_collection"
        return "unknown"

    def _record(self, method: str, node: ast.AST, receiver_kind: str) -> None:
        self.uses.append(
            ModelDmlUse(method=method, lineno=int(getattr(node, "lineno", 0)), receiver_kind=receiver_kind)
        )

    def _assign_target(self, target: ast.expr, kind: str) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = kind
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for index, element in enumerate(target.elts):
                element_kind = "instance" if kind == "instance_pair" and index == 0 else "unknown"
                self._assign_target(element, element_kind)

    def visit_Module(self, node: ast.Module) -> None:
        self._env_stack.append({alias: "model" for alias in self.model_aliases})
        for statement in node.body:
            self.visit(statement)
        self._env_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        registered_model = False
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            function_name = decorator.func.attr if isinstance(decorator.func, ast.Attribute) else ""
            if function_name == "register" and self._expr_kind(decorator.args[0]) == "model":
                registered_model = True
        self._admin_model_stack.append(registered_model)
        self._env_stack.append(dict(self.env))
        for statement in node.body:
            self.visit(statement)
        self._env_stack.pop()
        self._admin_model_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        function_env = {alias: "model" for alias in self.model_aliases}
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if _annotation_mentions_model(argument.annotation, self.model_aliases, self.model_name):
                function_env[argument.arg] = "instance"
            elif self._admin_model_stack and self._admin_model_stack[-1] and argument.arg == "queryset":
                function_env[argument.arg] = "queryset"
        self._env_stack.append(function_env)
        for statement in node.body:
            self.visit(statement)
        self._env_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        kind = self._expr_kind(node.value)
        for target in node.targets:
            self._assign_target(target, kind)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        kind = self._expr_kind(node.value)
        if _annotation_mentions_model(node.annotation, self.model_aliases, self.model_name):
            kind = "instance"
        self._assign_target(node.target, kind)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        iter_kind = self._expr_kind(node.iter)
        target_kind = "instance" if iter_kind in {"manager", "queryset", "instance_collection"} else "unknown"
        self._assign_target(node.target, target_kind)
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in DML_METHODS:
            receiver_kind = self._expr_kind(node.func.value)
            if receiver_kind in {"instance", "manager", "queryset"}:
                self._record(node.func.attr, node, receiver_kind)

        if isinstance(node.func, ast.Name) and node.func.id in self._proxy_helpers:
            for argument_index, methods in self._proxy_helpers[node.func.id].items():
                if argument_index >= len(node.args):
                    continue
                receiver_kind = self._expr_kind(node.args[argument_index])
                if receiver_kind not in {"instance", "manager", "queryset"}:
                    continue
                for method in methods:
                    self._record(method, node, f"proxy_{receiver_kind}")
        self.generic_visit(node)


def find_model_dml(
    source: str,
    *,
    model_name: str,
    filename: str = "<source>",
    relation_names: Iterable[str] = (),
) -> tuple[ModelDmlUse, ...]:
    tree = ast.parse(source, filename=filename)
    visitor = _ModelDmlVisitor(tree, model_name=model_name, relation_names=relation_names)
    visitor.visit(tree)
    return tuple(sorted(set(visitor.uses)))


def source_imports_model(source: str, *, model_name: str, filename: str = "<source>") -> bool:
    tree = ast.parse(source, filename=filename)
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == model_name for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "gameplay":
            module_aliases.update(alias.asname or alias.name for alias in node.names if alias.name == "models")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"gameplay.models", "gameplay.models.bots", "gameplay.models.arena_virtual"}:
                    module_aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == model_name:
            if _dotted_name(node.value) in module_aliases:
                return True
    return False


def iter_python_sources(root: Path, *, include_package_initializers: bool = False) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if "migrations" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name == "__init__.py" and not include_package_initializers:
            continue
        yield path
