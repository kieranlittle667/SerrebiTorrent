# Copyright (c) serrebidev and contributors
# SPDX-License-Identifier: MIT

"""Static inventory of canonical English strings exposed for translation.

The extractor uses Python's AST and a narrow parser for the Web PT_BR mapping,
so generating a POT does not require importing wxPython or the application.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
_TRANSLATION_CALL_NAMES = {
    "translate",
    "tr_main",
    "tr_remote",
    "tr_action",
    "tr_runtime",
    "tr_rss",
    "tr_create",
    "tr_update",
    "_magnet_text",
}
_EXTRA_MESSAGES = {
    # Translation Center presentation. These are direct wx labels today and are
    # kept explicit until that dialog itself is migrated to the shared runtime.
    "Translation Center",
    "Language code:",
    "Language name:",
    "Filter:",
    "Untranslated",
    "Needs review",
    "Translated",
    "Source text",
    "Source text:",
    "Translation",
    "Translation:",
    "Context / validation:",
    "Previous",
    "Save entry",
    "Next",
    "Export PO...",
    "Import PO...",
    "Open online translation",
    "Close",
    "Status",
    # Dynamic Web accessibility label pattern; the user-provided name remains
    # data and is substituted only after the action prefix is translated.
    "Select {name}",
}


def _string_key(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _target_names(node) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        result: set[str] = set()
        for item in node.elts:
            result.update(_target_names(item))
        return result
    return set()


def _call_name(func) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def python_source_messages(root: Path | None = None) -> set[str]:
    root = root or ROOT
    messages: set[str] = set(_EXTRA_MESSAGES)
    for path in sorted(root.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names: set[str] = set()
                for target in targets:
                    names.update(_target_names(target))
                value = node.value
                if any(name.startswith("_PT_BR") for name in names) and isinstance(value, ast.Dict):
                    for key in value.keys:
                        text = _string_key(key)
                        if text:
                            messages.add(text)
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name == "_" or name in _TRANSLATION_CALL_NAMES:
                    if node.args:
                        text = _string_key(node.args[0])
                        if text:
                            messages.add(text)
    return messages


def web_source_messages(root: Path | None = None) -> set[str]:
    root = root or ROOT
    path = root / "web_static" / "i18n.js"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    match = re.search(r"const\s+PT_BR\s*=\s*\{(?P<body>.*?)\n\s*\};", text, re.DOTALL)
    if not match:
        return set()
    messages: set[str] = set()
    for raw in match.group("body").splitlines():
        key = re.match(r"\s*'((?:\\.|[^'])*)'\s*:", raw)
        if not key:
            continue
        value = key.group(1)
        value = value.replace("\\'", "'").replace("\\n", "\n").replace("\\\\", "\\")
        if value:
            messages.add(value)
    return messages


def collect_source_messages(root: Path | None = None, *, include_web: bool = True) -> list[str]:
    root = root or ROOT
    messages = python_source_messages(root)
    if include_web:
        messages.update(web_source_messages(root))
        # Total order: casefold ties (e.g. 'Check for updates' vs
    # 'Check for Updates') must not depend on set iteration order,
    # which varies per process (hash randomization).
    return sorted(messages, key=lambda s: (s.casefold(), s))
