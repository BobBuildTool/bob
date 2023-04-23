from .EclipseCdtGenerator import eclipseCdtGenerator
from .QtCreatorGenerator import qtProjectGenerator
from .VisualStudioCode import vsCodeProjectGenerator
from ..utils import isWindows
import sys

__all__ = ['generators']

generators = {
    'eclipseCdt' :{
        'func' : eclipseCdtGenerator,
        'query' : False},
    'qt-creator' : {
        'func' : qtProjectGenerator,
        'query' : False},
    'vscode': {
        'func' : vsCodeProjectGenerator,
        'query' : False}
}

if isWindows():
    from .VisualStudio import vs2019ProjectGenerator
    generators['vs2019'] = {'func' : vs2019ProjectGenerator, 'query' : False}
