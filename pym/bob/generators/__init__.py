from .EclipseCdtGenerator import eclipseCdtGenerator
from .QtCreatorGenerator import qtProjectGenerator
from .VisualStudioCode import vsCodeProjectGenerator
from ..utils import isWindows
import sys

__all__ = ['generators']

generators = {
    'eclipseCdt' : eclipseCdtGenerator,
    'qt-creator' : qtProjectGenerator,
    'vscode': vsCodeProjectGenerator
}

if isWindows():
    from .VisualStudio import vs2019ProjectGenerator
    generators['vs2019'] = vs2019ProjectGenerator
