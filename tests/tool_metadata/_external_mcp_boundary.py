"""Exact MCP declaration roles, independent of Pilot's sealed authority catalog.

This is not a directory exemption: unrelated name routing and every existing
reflection/prefix/provider-registry gate still inspect the whole source tree.
"""

from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path

# Reviewed AST branches from production 9b0f1e7b, not regenerated at test time.
# Bodies are part of the exception: the read dispatch and DDL sentinel cannot
# silently become an execution route merely by retaining the same condition.
_BRANCH_FINGERPRINTS = {
    "get_application": "0d71be8be7d1069f99eed96f93c5888e10f05e4cbad5287a3a85cb0556ef7b1c",
    "list_applications": "9ac3cc84365ca0e988cd5cfc1bdf2cff0b06f6216a3a4d4966930f6eeef5caf0",
    "schema_upgrade": "d9fcad926b548f827d32d807775e0b2586a424f93c08e86fe89cd2b749f3e2dc",
}


def _exact_branch(statement: ast.If, role: str) -> bool:
    encoded = json.dumps(_semantic_shape(statement), sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest() == _BRANCH_FINGERPRINTS[role]


def _semantic_shape(node):
    # Ignore locations and empty optional AST fields, rather than relying on
    # ast.dump's presentation defaults across Python versions.
    if isinstance(node, ast.AST):
        return [
            type(node).__name__,
            {
                key: _semantic_shape(value)
                for key, value in ast.iter_fields(node)
                if value is not None and value != []
            },
        ]
    if isinstance(node, list):
        return [_semantic_shape(value) for value in node]
    return node


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    while node in parents:
        node = parents[node]
        yield node


def external_literal_role(
    path: Path, node: ast.AST, parents: dict[ast.AST, ast.AST], src: Path
) -> bool:
    relative = path.relative_to(src).as_posix() if path.is_relative_to(src) else ""
    if relative not in {
        "external_agents/catalog.py",
        "external_agents/schemas.py",
        "external_agents/server.py",
        "external_agents/service.py",
        "external_agents/migration.py",
    }:
        return False  # No exception; the caller continues every original guard.
    declarations = {
        "external_agents/catalog.py": {"BUSINESS_PROPOSALS", "TOOL_CAPABILITIES"},
        "external_agents/schemas.py": {"INPUTS", "OUTPUTS"},
        "external_agents/server.py": {"DESCRIPTIONS"},
    }.get(relative, set())
    for owner in _ancestors(node, parents):
        if isinstance(owner, (ast.Assign, ast.AnnAssign)):
            targets = owner.targets if isinstance(owner, ast.Assign) else [owner.target]
            if (
                isinstance(parents.get(owner), ast.Module)
                and len(targets) == 1
                and isinstance(targets[0], ast.Name)
                and targets[0].id in declarations
                and isinstance(owner.value, ast.Dict)
                and node in ast.walk(owner.value)
            ):
                return True
            parent = parents.get(owner)
            if (
                relative == "external_agents/schemas.py"
                and isinstance(owner, ast.AnnAssign)
                and isinstance(owner.target, ast.Name)
                and owner.target.id == "action"
                and isinstance(parent, ast.ClassDef)
                and parent.name == "RequestData"
                and any(
                    isinstance(base, ast.Name) and base.id == "StrictInput" for base in parent.bases
                )
                and isinstance(owner.annotation, ast.Subscript)
                and isinstance(owner.annotation.value, ast.Name)
                and owner.annotation.value.id == "Literal"
                and node in ast.walk(owner.annotation)
            ):
                return True
        if isinstance(owner, ast.Call):
            statement = parents.get(owner)
            if (
                isinstance(statement, ast.Expr)
                and isinstance(parents.get(statement), ast.Module)
                and isinstance(owner.func, ast.Attribute)
                and owner.func.attr == "update"
                and isinstance(owner.func.value, ast.Name)
                and owner.func.value.id in declarations
                and len(owner.args) == 1
                and not owner.keywords
                and isinstance(owner.args[0], ast.Dict)
                and node in ast.walk(owner.args[0])
            ):
                return True
        if not isinstance(owner, ast.Compare) or len(owner.ops) != 1 or len(owner.comparators) != 1:
            continue
        function = next(
            (item for item in _ancestors(owner, parents) if isinstance(item, ast.FunctionDef)), None
        )
        if function is None:
            continue
        statement = parents.get(owner)
        if not isinstance(statement, ast.If) or statement.test is not owner:
            continue
        right = owner.comparators[0]
        if (
            relative == "external_agents/catalog.py"
            and function.name == "permitted"
            and isinstance(parents.get(function), ast.Module)
            and isinstance(owner.ops[0], ast.In)
            and isinstance(owner.left, ast.Name)
            and owner.left.id == "tool"
            and isinstance(right, ast.Set)
            and all(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in right.elts
            )
            and {item.value for item in right.elts}
            == {
                "list_applications",
                "get_application",
                "list_events",
                "get_event",
                "get_request",
                "cancel_request",
            }
            and not statement.orelse
            and len(statement.body) == 1
            and isinstance(statement.body[0], ast.Return)
            and isinstance(statement.body[0].value, ast.Constant)
            and statement.body[0].value.value is True
        ):
            return True
        parent_class = parents.get(function)
        if (
            relative == "external_agents/service.py"
            and isinstance(parent_class, ast.ClassDef)
            and parent_class.name == "ExternalAgentService"
            and isinstance(owner.ops[0], ast.Eq)
            and isinstance(right, ast.Constant)
        ):
            if (
                function.name == "call"
                and isinstance(owner.left, ast.Name)
                and owner.left.id == "tool"
                and right.value in {"get_application", "list_applications"}
                and _exact_branch(statement, right.value)
            ):
                return True
            statement = parents.get(owner)
            if (
                function.name == "_decide_business"
                and ast.unparse(owner.left) == "request.action"
                and right.value == "create_application"
                and isinstance(statement, ast.If)
                and not statement.orelse
                and len(statement.body) == 1
                and isinstance(statement.body[0], ast.Assign)
                and _semantic_shape(statement.body[0])
                == _semantic_shape(
                    ast.parse("request.application_id = outcome['application']['id']").body[0],
                )
            ):
                return True
        if (
            relative == "external_agents/migration.py"
            and function.name == "ensure_business_schema"
            and isinstance(parents.get(function), ast.Module)
            and isinstance(owner.ops[0], ast.NotIn)
            and isinstance(owner.left, ast.Constant)
            and owner.left.value == "create_application"
            and isinstance(right, ast.Name)
            and right.id == "sql"
            and _exact_branch(statement, "schema_upgrade")
        ):
            return True
    return False


_AUTHORITY = (
    "offerpilot.ai.tool_specs",
    "offerpilot.ai.tool_runtime",
    "offerpilot.ai.tool_authority",
    "offerpilot.ai.write_operations",
    "offerpilot.ai.confirmation",
    "offerpilot.ai.confirmation_receipt",
    "offerpilot.ai.agent_contracts",
    "offerpilot.ai.pending_replay",
    "offerpilot.ai.deterministic_actions",
    "offerpilot.agent_runtime",
    "offerpilot.pilot_runtime",
    "offerpilot.product_actions",
)
# Separate product promise: this MCP server does not call OfferPilot models.
# Pure shared data such as ai.types remains usable; future utilities are not
# automatically treated as sealed authority or model execution entrypoints.
_NO_MODEL_RUNTIME = tuple(
    "offerpilot.ai." + name
    for name in (
        "client",
        "agent_loop",
        "workflows",
        "provider_boundaries",
        "provider_stream",
        "interview_knowledge_capture",
        "interview_preparation_proposals",
        "interview_review_proposals",
        "interview_stories",
        "material_proposals",
        "mock_interview",
        "offer_negotiation",
        "opportunity_fit_reviews",
        "resume_structured_import",
    )
)
_DYNAMIC_MODULES = ("importlib", "runpy", "pkgutil", "builtins")
_LOADER_MEMBERS = {
    "create_module",
    "exec_module",
    "load_module",
    "run_module",
    "run_path",
    "spec_from_file_location",
    "module_from_spec",
}
# These loader execution members are reserved in the protected scopes. Unknown
# receivers fail closed; there are no current business-method collisions.
_EXTERNAL = "offerpilot.external_agents"


def _within(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def authority_dependency_violations(path: Path, source: str, src: Path) -> list[str]:
    if not path.is_relative_to(src):
        return []
    package = ".".join((src.name, *path.parent.relative_to(src).parts))
    module_name = package if path.stem == "__init__" else package + "." + path.stem
    external = _within(module_name, _EXTERNAL)
    authority = any(_within(module_name, prefix) for prefix in _AUTHORITY)
    if not external and not authority:
        return []  # db.py and CLI are legitimate integration/owner entrypoints.
    tree = ast.parse(source)
    violations = []
    aliases: dict[str, str] = {}
    strings: dict[str, str] = {}

    def forbidden(module: str) -> bool:
        return (
            any(_within(module, prefix) for prefix in (*_AUTHORITY, *_NO_MODEL_RUNTIME))
            if external
            else _within(module, _EXTERNAL)
        )

    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            modules = [
                (
                    alias.name,
                    alias.asname or alias.name.split(".")[0],
                    alias.name if alias.asname else alias.name.split(".")[0],
                )
                for alias in item.names
            ]
        elif isinstance(item, ast.ImportFrom):
            parts = package.split(".")
            base = ".".join(parts[: len(parts) - item.level + 1]) if item.level else ""
            module = ".".join(part for part in (base, item.module or "") if part)
            if module in {"offerpilot", "offerpilot.ai"} and any(
                alias.name == "*" for alias in item.names
            ):
                violations.append(f"authority-root-star-import:{item.lineno}")
            modules = [
                (module, alias.asname or alias.name, module + "." + alias.name)
                for alias in item.names
            ]
            modules += [
                (module + "." + alias.name, alias.asname or alias.name, module + "." + alias.name)
                for alias in item.names
            ]
        else:
            continue
        for module, alias, qualified in modules:
            aliases[alias] = qualified
            if forbidden(module):
                violations.append(f"cross-authority-import:{item.lineno}:{module}")
            if external and any(_within(module, prefix) for prefix in _DYNAMIC_MODULES):
                violations.append(f"external-dynamic-namespace:{item.lineno}:{module}")
            if _within(module, "sys.modules"):
                violations.append(f"authority-module-cache:{item.lineno}")

    def string(item: ast.AST) -> str | None:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            return item.value
        if isinstance(item, ast.Name):
            return strings.get(item.id)
        if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Add):
            left, right = string(item.left), string(item.right)
            return left + right if left is not None and right is not None else None
        return None

    def qualified(item: ast.AST) -> str:
        if isinstance(item, ast.Name):
            if item.id == "__builtins__":
                return "builtins"
            return aliases.get(
                item.id,
                item.id if item.id in {"__import__", "exec", "eval", "getattr", "vars"} else "",
            )
        if isinstance(item, ast.Attribute):
            base = qualified(item.value)
            return base + "." + item.attr if base else ""
        if (
            isinstance(item, ast.Call)
            and qualified(item.func) in {"getattr", "builtins.getattr"}
            and len(item.args) == 2
        ):
            name = string(item.args[1])
            base = qualified(item.args[0])
            return base + "." + name if base and name else ""
        if isinstance(item, ast.Subscript):
            base, name = qualified(item.value), string(item.slice)
            return "builtins." + name if base in {"builtins", "builtins.__dict__"} and name else ""
        if (
            isinstance(item, ast.Call)
            and qualified(item.func) in {"builtins.get", "builtins.__dict__.get"}
            and item.args
        ):
            name = string(item.args[0])
            return "builtins." + name if name else ""
        if isinstance(item, ast.Call) and qualified(item.func) in {
            "__import__",
            "builtins.__import__",
            "importlib.import_module",
        }:
            return module_argument(item) or ""
        return ""

    def module_argument(item: ast.Call) -> str | None:
        argument = (
            item.args[0]
            if item.args
            else next((keyword.value for keyword in item.keywords if keyword.arg == "name"), None)
        )
        return string(argument) if argument is not None else None

    # Resolve local aliases and concatenated module names, without executing source.
    for _ in range(64):
        previous = (dict(aliases), dict(strings))
        for item in ast.walk(tree):
            if isinstance(item, (ast.Assign, ast.AnnAssign)) and item.value is not None:
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        name, value = qualified(item.value), string(item.value)
                        if name:
                            aliases[target.id] = name
                        if value is not None:
                            strings[target.id] = value
        if previous == (aliases, strings):
            break
    else:
        violations.append("authority-alias-resolution-unbounded")
    for item in ast.walk(tree):
        if _within(qualified(item), "sys.modules") or _within(qualified(item), "sys.__dict__"):
            violations.append(f"authority-module-cache:{getattr(item, 'lineno', 0)}")
        if (
            isinstance(item, ast.Call)
            and qualified(item.func) in {"vars", "builtins.vars"}
            and item.args
            and qualified(item.args[0]) == "sys"
        ):
            violations.append(f"authority-module-cache:{item.lineno}:vars(sys)")
        if (
            external
            and isinstance(item, ast.Name)
            and item.id
            in {
                "__builtins__",
                "__import__",
                "exec",
                "eval",
                "compile",
                "globals",
                "locals",
                "vars",
            }
        ):
            violations.append(f"external-dynamic-builtin:{item.lineno}:{item.id}")
        if isinstance(item, ast.Attribute) and item.attr in _LOADER_MEMBERS:
            violations.append(f"dynamic-loader-reference:{item.lineno}:{item.attr}")
        if isinstance(item, ast.Attribute) and forbidden(qualified(item)):
            violations.append(f"cross-authority-reference:{item.lineno}:{qualified(item)}")
        if not isinstance(item, ast.Call):
            continue
        name = qualified(item.func)
        if name.rsplit(".", 1)[-1] in _LOADER_MEMBERS:
            violations.append(f"dynamic-loader-call:{item.lineno}:{name}")
        if name in {"exec", "eval", "builtins.exec", "builtins.eval"}:
            violations.append(f"dynamic-authority-code:{item.lineno}:{name}")
        if name in {"__import__", "builtins.__import__", "importlib.import_module"}:
            target = module_argument(item)
            if external or target is None or forbidden(target):
                violations.append(f"dynamic-authority-import:{item.lineno}:{name}")
    return violations
