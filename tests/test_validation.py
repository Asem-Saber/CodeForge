from src.tools.validation import validate_python_syntax, validate_imports, _check_imports_raw


class TestValidatePythonSyntax:
    def test_valid_code(self):
        result = validate_python_syntax.invoke({"code": "x = 1 + 2\nprint(x)"})
        assert result == "Syntax is valid."

    def test_syntax_error(self):
        result = validate_python_syntax.invoke({"code": "def foo(\n"})
        assert "Syntax error" in result

    def test_empty_code(self):
        result = validate_python_syntax.invoke({"code": ""})
        assert result == "Syntax is valid."

    def test_multiline_valid(self):
        code = "def greet(name):\n    return f'Hello, {name}'\n\nprint(greet('world'))"
        result = validate_python_syntax.invoke({"code": code})
        assert result == "Syntax is valid."

    def test_indentation_error(self):
        code = "def foo():\nprint('bad')"
        result = validate_python_syntax.invoke({"code": code})
        assert "Syntax error" in result


class TestValidateImports:
    def test_stdlib_imports(self):
        result = validate_imports.invoke({"code": "import os\nimport json\nimport sys"})
        assert result == "All imports are valid."

    def test_nonexistent_module(self):
        result = validate_imports.invoke({"code": "import nonexistent_module_xyz_123"})
        assert "Import issues" in result
        assert "nonexistent_module_xyz_123" in result

    def test_from_import_valid(self):
        result = validate_imports.invoke({"code": "from os.path import join"})
        assert result == "All imports are valid."

    def test_from_import_invalid(self):
        result = validate_imports.invoke({"code": "from fake_package_abc import something"})
        assert "Import issues" in result

    def test_no_imports(self):
        result = validate_imports.invoke({"code": "x = 42"})
        assert result == "All imports are valid."


class TestCheckImportsRaw:
    def test_returns_empty_for_valid(self):
        issues = _check_imports_raw("import os")
        assert issues == []

    def test_returns_list_for_invalid(self):
        issues = _check_imports_raw("import nonexistent_xyz")
        assert len(issues) == 1
        assert "nonexistent_xyz" in issues[0]

    def test_multiple_bad_imports(self):
        code = "import fake_one\nimport fake_two"
        issues = _check_imports_raw(code)
        assert len(issues) == 2
