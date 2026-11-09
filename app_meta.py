"""Metadados do TELECOPY.

Módulo próprio (e não `__init__.py`) porque um `__init__` de topo nível não é
instalável como módulo: quando o projeto era instalado como wheel, `core.py`
fazia `import __init__` e morria com ModuleNotFoundError. Aqui os metadados
funcionam tanto no checkout quanto instalado.

Mantenha `__version__` em sincronia com `version` em pyproject.toml.
"""

__version__ = "4.1.0"
__author__ = "montx2"
__license__ = "MIT"
__all__ = ["__version__", "__author__", "__license__"]
