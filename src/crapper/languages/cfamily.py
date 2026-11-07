"""C/C++ functions, namespaces, and cyclomatic complexity, without the preprocessor."""

from pathlib import Path

from crapper.languages.treesitter import (
    binary_logic, complexity, end_line, node_text, parse, start_line,
)
from crapper.model import Function

_SCOPES = {'namespace_definition', 'class_specifier', 'struct_specifier', 'union_specifier'}
_DECISIONS = {
    'if_statement', 'for_statement', 'for_range_loop', 'while_statement',
    'do_statement', 'case_statement', 'conditional_expression', 'catch_clause',
}


def _decision(node):
    if node.type.startswith('preproc_'):
        return False
    return node.type in _DECISIONS or binary_logic(node) or (
        node.type == 'binary_expression'
        and any(child.type in {'and', 'or'} for child in node.children)
    )


def _skip_decisions(node):
    if node.type in {'static_assert_declaration', 'requires_expression'}:
        return True
    parent = node.parent
    if parent is None or parent.type not in {'preproc_if', 'preproc_elif'}:
        return False
    condition = parent.child_by_field_name('condition')
    return condition is not None and condition.id == node.id


def _declarator_name(data, node):
    while node is not None:
        if node.type in {'identifier', 'field_identifier', 'operator_name', 'destructor_name'}:
            return None, node_text(data, node)
        if node.type == 'qualified_identifier':
            scope = node.child_by_field_name('scope')
            nested, name = _declarator_name(data, node.child_by_field_name('name'))
            owner = node_text(data, scope) if scope else '::'
            if nested:
                owner += '::' + nested
            return owner, name
        if node.type == 'operator_cast':
            target = node.child_by_field_name('type')
            return None, 'operator ' + node_text(data, target)
        nested = node.child_by_field_name('declarator')
        if nested is None and node.type == 'parenthesized_declarator':
            nested = next(iter(node.named_children), None)
        node = nested
    return None, ''


def _scope(data, node):
    parts = []
    current = node.parent
    while current is not None:
        if current.type in _SCOPES:
            name = current.child_by_field_name('name')
            parts.append(node_text(data, name) if name else '(anonymous)')
        current = current.parent
    return list(reversed(parts))


def _module(path, project_root):
    located = Path(path)
    if project_root:
        try:
            return located.resolve().relative_to(Path(project_root).resolve()).as_posix()
        except ValueError:
            pass
    return located.as_posix()


def _identity(data, node, path, project_root):
    owner, name = _declarator_name(data, node.child_by_field_name('declarator'))
    scopes = _scope(data, node)
    if owner is not None and owner.startswith('::'):
        scopes = []
    if owner and owner.lstrip(':'):
        scopes.append(owner.lstrip(':'))
    return name, '::'.join(scopes) or _module(path, project_root)


def _functions(source, path, project_root, language):
    data, tree = parse(source, language)
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == 'function_definition':
            name, namespace = _identity(data, node, path, project_root)
            if name:
                found.append(Function(
                    name=name, namespace=namespace,
                    complexity=complexity(node, _decision, _skip_decisions),
                    start_line=start_line(node), end_line=end_line(node),
                    path=path, language=language,
                    start_byte=node.start_byte, end_byte=node.end_byte,
                ))
            continue
        stack.extend(reversed(node.children))
    return found


def c_functions(source, path, project_root=None):
    return _functions(source, path, project_root, 'c')


def cpp_functions(source, path, project_root=None):
    return _functions(source, path, project_root, 'cpp')
