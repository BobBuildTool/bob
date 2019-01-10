from .EclipseCdtGenerator import eclipseCdtGenerator
from .QtCreatorGenerator import qtProjectGenerator

__all__ = ['generators']

generators = {
    'eclipseCdt' : eclipseCdtGenerator,
    'qt-creator' : qtProjectGenerator,
}
