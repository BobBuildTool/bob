from .EclipseCdtGenerator import eclipseCdtGenerator
from .QtCreatorGenerator import qtProjectGenerator
import sys

__all__ = ['generators']

generators = {
    'eclipseCdt' : eclipseCdtGenerator,
    'qt-creator' : qtProjectGenerator,
}

if sys.platform == 'win32' or sys.platform == 'msys':
    from .VisualStudio import vs2019ProjectGenerator
    generators['vs2019'] = vs2019ProjectGenerator
