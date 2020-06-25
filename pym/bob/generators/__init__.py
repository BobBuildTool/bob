from .EclipseCdtGenerator import eclipseCdtGenerator
from .QtCreatorGenerator import qtProjectGenerator
from .VsCode import vscodeProjectGenerator
import sys

__all__ = ['generators']

generators = {
    'eclipseCdt' : eclipseCdtGenerator,
    'qt-creator' : qtProjectGenerator,
    'vscode' : vscodeProjectGenerator,
}

if sys.platform == 'win32' or sys.platform == 'msys':
    from .VisualStudio import vs2019ProjectGenerator
    generators['vs2019'] = vs2019ProjectGenerator
