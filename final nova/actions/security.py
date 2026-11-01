import ast


BLOCKED_IMPORTS = {
    "os",
    "subprocess",
    "socket",
    "ctypes",
    "winreg",
    "shutil",
    "psutil",
}

BLOCKED_FUNCTIONS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "system",
    "popen",
}

BLOCKED_METHODS = {
    "unlink",
    "remove",
    "rmdir",
    "rmtree",
    "rename",
    "replace",
    "move",
}

BLOCKED_KEYWORDS = {
    "powershell",
    "cmd.exe",
    "format c:",
    "reg delete",
    "schtasks",
}


def check_code(code: str):
    """
    Check AI-generated Python code before execution.

    Returns:
        (True, "Safe")
        or
        (False, "Reason")
    """

    lower_code = code.lower()

    for keyword in BLOCKED_KEYWORDS:
        if keyword in lower_code:
            return False, f"Blocked keyword: {keyword}"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Invalid Python code: {e}"

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):

            for alias in node.names:

                module = alias.name.split(".")[0]

                if module in BLOCKED_IMPORTS:
                    return False, f"Blocked import: {module}"

        elif isinstance(node, ast.ImportFrom):

            if node.module:

                module = node.module.split(".")[0]

                if module in BLOCKED_IMPORTS:
                    return False, f"Blocked import: {module}"

        elif isinstance(node, ast.Call):

            if isinstance(node.func, ast.Name):

                if node.func.id in BLOCKED_FUNCTIONS:
                    return False, f"Blocked function: {node.func.id}"

            elif isinstance(node.func, ast.Attribute):

                if node.func.attr in BLOCKED_METHODS:
                    return False, f"Blocked method: {node.func.attr}"

    return True, "Code passed security checks"