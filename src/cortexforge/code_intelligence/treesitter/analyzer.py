"""Tree-sitter concrete AST analyzer supporting Python, TS/JS, Go, and Java."""

import hashlib
from typing import ClassVar

import tree_sitter
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Parser

from cortexforge.code_intelligence.parser import (
    CodeIntelligenceProvider,
    ParsedRelationship,
    ParsedSymbol,
    ParseResult,
)


class TreeSitterProvider(CodeIntelligenceProvider):
    """Multi-language AST analyzer using Tree-sitter grammars."""

    EXT_TO_LANG: ClassVar[dict[str, str]] = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".go": "go",
        ".java": "java",
    }

    def __init__(self) -> None:
        self._languages: dict[str, Language] = {
            "python": Language(tree_sitter_python.language()),
            "javascript": Language(tree_sitter_javascript.language()),
            "typescript": Language(tree_sitter_typescript.language_typescript()),
            "tsx": Language(tree_sitter_typescript.language_tsx()),
            "go": Language(tree_sitter_go.language()),
            "java": Language(tree_sitter_java.language()),
        }

    def can_parse(self, file_path: str) -> bool:
        ext = self._get_ext(file_path)
        return ext in self.EXT_TO_LANG

    def _get_ext(self, file_path: str) -> str:
        idx = file_path.rfind(".")
        return file_path[idx:].lower() if idx != -1 else ""

    def parse_source(self, file_path: str, content: bytes) -> ParseResult:
        ext = self._get_ext(file_path)
        lang_key = self.EXT_TO_LANG.get(ext)
        if not lang_key:
            return ParseResult(language="unknown", error=f"Unsupported file extension: {ext}")

        language = self._languages[lang_key]
        parser = Parser(language)
        tree = parser.parse(content)

        symbols: list[ParsedSymbol] = []
        relationships: list[ParsedRelationship] = []

        file_hash = hashlib.sha256(content).hexdigest()
        file_symbol = ParsedSymbol(
            entity_type="file",
            name=file_path.split("/")[-1].split("\\")[-1],
            qualified_name=file_path,
            file_path=file_path,
            start_line=1,
            end_line=content.count(b"\n") + 1,
            signature=None,
            content_hash=file_hash,
            language=lang_key,
            metadata={"size_bytes": len(content)},
        )
        symbols.append(file_symbol)

        if lang_key == "python":
            self._parse_python(tree.root_node, content, file_path, symbols, relationships)
        elif lang_key in ("javascript", "typescript", "tsx"):
            self._parse_ts_js(tree.root_node, content, file_path, lang_key, symbols, relationships)
        elif lang_key == "go":
            self._parse_go(tree.root_node, content, file_path, symbols, relationships)
        elif lang_key == "java":
            self._parse_java(tree.root_node, content, file_path, symbols, relationships)

        return ParseResult(symbols=symbols, relationships=relationships, language=lang_key)

    # ------------------ PYTHON PARSING ------------------
    def _parse_python(
        self,
        root_node: tree_sitter.Node,
        content: bytes,
        file_path: str,
        symbols: list[ParsedSymbol],
        relationships: list[ParsedRelationship],
    ) -> None:
        def visit(node: tree_sitter.Node, parent_scope: str = "") -> None:
            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    class_name = content[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    qualified = f"{parent_scope}.{class_name}" if parent_scope else f"{file_path}:{class_name}"
                    class_content = content[node.start_byte : node.end_byte]
                    class_hash = hashlib.sha256(class_content).hexdigest()

                    # Bases
                    bases = []
                    args_node = node.child_by_field_name("superclasses")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type in ("identifier", "attribute"):
                                base_name = content[arg.start_byte : arg.end_byte].decode(
                                    "utf-8", errors="replace"
                                )
                                bases.append(base_name)
                                relationships.append(
                                    ParsedRelationship(
                                        source_qualified_name=qualified,
                                        target_qualified_name=base_name,
                                        relationship_type="inherits",
                                    )
                                )

                    symbols.append(
                        ParsedSymbol(
                            entity_type="class",
                            name=class_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"class {class_name}({', '.join(bases)})" if bases else f"class {class_name}",
                            content_hash=class_hash,
                            language="python",
                            metadata={"bases": bases},
                        )
                    )
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=file_path,
                            target_qualified_name=qualified,
                            relationship_type="contains",
                        )
                    )

                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            visit(child, qualified)
                    return

            elif node.type in ("function_definition", "async_function_definition"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    func_name = content[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    qualified = f"{parent_scope}.{func_name}" if parent_scope else f"{file_path}:{func_name}"
                    func_content = content[node.start_byte : node.end_byte]
                    func_hash = hashlib.sha256(func_content).hexdigest()
                    params_node = node.child_by_field_name("parameters")
                    params_str = (
                        content[params_node.start_byte : params_node.end_byte].decode("utf-8", errors="replace")
                        if params_node
                        else "()"
                    )
                    entity_type = "method" if parent_scope else "function"
                    is_async = node.type == "async_function_definition"

                    symbols.append(
                        ParsedSymbol(
                            entity_type=entity_type,
                            name=func_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"{'async ' if is_async else ''}def {func_name}{params_str}",
                            content_hash=func_hash,
                            language="python",
                            metadata={"is_async": is_async},
                        )
                    )
                    source_container = parent_scope if parent_scope else file_path
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=source_container,
                            target_qualified_name=qualified,
                            relationship_type="contains",
                        )
                    )
                return

            elif node.type == "import_statement":
                # import foo, import foo.bar as baz
                for child in node.children:
                    if child.type == "dotted_name":
                        mod = content[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
                        relationships.append(
                            ParsedRelationship(
                                source_qualified_name=file_path,
                                target_qualified_name=mod,
                                relationship_type="imports",
                            )
                        )
                    elif child.type == "aliased_import":
                        name = child.child_by_field_name("name")
                        if name:
                            mod = content[name.start_byte : name.end_byte].decode("utf-8", errors="replace")
                            relationships.append(
                                ParsedRelationship(
                                    source_qualified_name=file_path,
                                    target_qualified_name=mod,
                                    relationship_type="imports",
                                )
                            )

            elif node.type == "import_from_statement":
                # from foo import bar
                module_node = node.child_by_field_name("module_name")
                if module_node:
                    mod = content[module_node.start_byte : module_node.end_byte].decode("utf-8", errors="replace")
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=file_path,
                            target_qualified_name=mod,
                            relationship_type="imports",
                        )
                    )

            for child in node.children:
                visit(child, parent_scope)

        visit(root_node)

    # ------------------ TS/JS PARSING ------------------
    def _parse_ts_js(
        self,
        root_node: tree_sitter.Node,
        content: bytes,
        file_path: str,
        lang: str,
        symbols: list[ParsedSymbol],
        relationships: list[ParsedRelationship],
    ) -> None:
        def visit(node: tree_sitter.Node, parent_scope: str = "") -> None:
            if node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    class_name = content[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
                    qualified = f"{parent_scope}.{class_name}" if parent_scope else f"{file_path}:{class_name}"
                    class_hash = hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest()

                    symbols.append(
                        ParsedSymbol(
                            entity_type="class",
                            name=class_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"class {class_name}",
                            content_hash=class_hash,
                            language=lang,
                        )
                    )
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=file_path,
                            target_qualified_name=qualified,
                            relationship_type="contains",
                        )
                    )
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            visit(child, qualified)
                    return

            elif node.type == "interface_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    iface_name = content[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
                    qualified = f"{parent_scope}.{iface_name}" if parent_scope else f"{file_path}:{iface_name}"
                    symbols.append(
                        ParsedSymbol(
                            entity_type="interface",
                            name=iface_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"interface {iface_name}",
                            content_hash=hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest(),
                            language=lang,
                        )
                    )
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=file_path,
                            target_qualified_name=qualified,
                            relationship_type="contains",
                        )
                    )
                    return

            elif node.type in ("function_declaration", "method_definition"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    func_name = content[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
                    qualified = f"{parent_scope}.{func_name}" if parent_scope else f"{file_path}:{func_name}"
                    symbols.append(
                        ParsedSymbol(
                            entity_type="method" if parent_scope else "function",
                            name=func_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"function {func_name}()",
                            content_hash=hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest(),
                            language=lang,
                        )
                    )
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=parent_scope or file_path,
                            target_qualified_name=qualified,
                            relationship_type="contains",
                        )
                    )
                return

            elif node.type == "import_statement":
                source_node = node.child_by_field_name("source")
                if source_node:
                    src_text = content[source_node.start_byte : source_node.end_byte].decode("utf-8", errors="replace").strip("\"'")
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=file_path,
                            target_qualified_name=src_text,
                            relationship_type="imports",
                        )
                    )

            for child in node.children:
                visit(child, parent_scope)

        visit(root_node)

    # ------------------ GO PARSING ------------------
    def _parse_go(
        self,
        root_node: tree_sitter.Node,
        content: bytes,
        file_path: str,
        symbols: list[ParsedSymbol],
        relationships: list[ParsedRelationship],
    ) -> None:
        def visit(node: tree_sitter.Node) -> None:
            if node.type == "package_clause":
                pkg_id = node.child_by_field_name("package") or (node.children[1] if len(node.children) > 1 else None)
                if pkg_id:
                    pkg_name = content[pkg_id.start_byte : pkg_id.end_byte].decode("utf-8", errors="replace")
                    symbols.append(
                        ParsedSymbol(
                            entity_type="module",
                            name=pkg_name,
                            qualified_name=f"{file_path}:pkg:{pkg_name}",
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"package {pkg_name}",
                            content_hash=hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest(),
                            language="go",
                        )
                    )

            elif node.type == "function_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    func_name = content[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
                    qualified = f"{file_path}:{func_name}"
                    symbols.append(
                        ParsedSymbol(
                            entity_type="function",
                            name=func_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"func {func_name}",
                            content_hash=hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest(),
                            language="go",
                        )
                    )
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=file_path,
                            target_qualified_name=qualified,
                            relationship_type="contains",
                        )
                    )

            elif node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                receiver = node.child_by_field_name("receiver")
                recv_text = (
                    content[receiver.start_byte : receiver.end_byte].decode("utf-8", errors="replace")
                    if receiver
                    else ""
                )
                if name_node:
                    m_name = content[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
                    qualified = f"{file_path}:{m_name}"
                    symbols.append(
                        ParsedSymbol(
                            entity_type="method",
                            name=m_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"func {recv_text} {m_name}",
                            content_hash=hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest(),
                            language="go",
                        )
                    )

            elif node.type == "type_spec":
                name_node = node.child_by_field_name("name")
                type_node = node.child_by_field_name("type")
                if name_node:
                    type_name = content[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
                    is_interface = type_node and type_node.type == "interface_type"
                    kind = "interface" if is_interface else "model"
                    qualified = f"{file_path}:{type_name}"
                    symbols.append(
                        ParsedSymbol(
                            entity_type=kind,
                            name=type_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"type {type_name}",
                            content_hash=hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest(),
                            language="go",
                        )
                    )

            elif node.type == "import_spec":
                path_node = node.child_by_field_name("path")
                if path_node:
                    import_path = content[path_node.start_byte : path_node.end_byte].decode("utf-8", errors="replace").strip("\"`")
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=file_path,
                            target_qualified_name=import_path,
                            relationship_type="imports",
                        )
                    )

            for child in node.children:
                visit(child)

        visit(root_node)

    # ------------------ JAVA PARSING ------------------
    def _parse_java(
        self,
        root_node: tree_sitter.Node,
        content: bytes,
        file_path: str,
        symbols: list[ParsedSymbol],
        relationships: list[ParsedRelationship],
    ) -> None:
        def visit(node: tree_sitter.Node, parent_scope: str = "") -> None:
            if node.type in ("class_declaration", "interface_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name_str = content[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
                    kind = "interface" if node.type == "interface_declaration" else "class"
                    qualified = f"{parent_scope}.{name_str}" if parent_scope else f"{file_path}:{name_str}"
                    symbols.append(
                        ParsedSymbol(
                            entity_type=kind,
                            name=name_str,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"{kind} {name_str}",
                            content_hash=hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest(),
                            language="java",
                        )
                    )
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=file_path,
                            target_qualified_name=qualified,
                            relationship_type="contains",
                        )
                    )
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            visit(child, qualified)
                    return

            elif node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    m_name = content[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
                    qualified = f"{parent_scope}.{m_name}" if parent_scope else f"{file_path}:{m_name}"
                    symbols.append(
                        ParsedSymbol(
                            entity_type="method",
                            name=m_name,
                            qualified_name=qualified,
                            file_path=file_path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            signature=f"{m_name}()",
                            content_hash=hashlib.sha256(content[node.start_byte : node.end_byte]).hexdigest(),
                            language="java",
                        )
                    )
                    relationships.append(
                        ParsedRelationship(
                            source_qualified_name=parent_scope or file_path,
                            target_qualified_name=qualified,
                            relationship_type="contains",
                        )
                    )
                return

            elif node.type == "import_declaration":
                for child in node.children:
                    if child.type in ("scoped_identifier", "identifier"):
                        import_str = content[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
                        relationships.append(
                            ParsedRelationship(
                                source_qualified_name=file_path,
                                target_qualified_name=import_str,
                                relationship_type="imports",
                            )
                        )

            for child in node.children:
                visit(child, parent_scope)

        visit(root_node)
