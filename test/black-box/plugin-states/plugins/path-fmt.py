# Sample plugin to modify path name calculation
#
# This plugin introduces two new properties to a recipe:
#  - CheckoutDir: optional string that is appended to the source directory
#  - Platform: optional string that is appenden to the build and dist directories

from os.path import join
from bob.errors import ParseError
from bob.input import PluginState, PluginProperty

def commonFormatter(step, states):
    s = states['PathFmt']
    if step.isCheckoutStep():
        base = step.getPackage().getRecipe().getName()
        ext = s.getCheckoutDir()
        ret = base+"-"+ext if ext else base
    else:
        base = step.getPackage().getName()
        ext = s.getPlatformDir()
        ret = join(base, ext) if ext else base
    return ret.replace('::', "/")

def releaseFormatter(step, states):
    return join("work", commonFormatter(step, states), step.getLabel())

def developFormatter(step, states):
    return join("dev", step.getLabel(), commonFormatter(step, states))

def jenkinsFormatter(step, states):
    return join(commonFormatter(step, states), step.getLabel())

class PathFmtState(PluginState):
    def __init__(self):
        self.__checkoutDir = None
        self.__platformDir = None

    def onEnter(self, env, properties):
        # CheckoutDir is always taken from current recipe
        self.__checkoutDir = properties['CheckoutDir'].getValue()
        if self.__checkoutDir is not None:
            self.__checkoutDir = env.substitute(self.__checkoutDir, "CheckoutDir")

        # Platform is passed down to dependencies
        platform = properties['Platform']
        if platform.isPresent():
            self.__platformDir = env.substitute(platform.getValue(), "Platform")

    def getCheckoutDir(self):
        return self.__checkoutDir

    def getPlatformDir(self):
        return self.__platformDir


class StringProperty(PluginProperty):
    @staticmethod
    def validate(data):
        return isinstance(data, str)


manifest = {
    'apiVersion' : "0.20",
    'hooks' : {
        'releaseNameFormatter' : releaseFormatter,
        'developNameFormatter' : developFormatter,
        'jenkinsNameFormatter' : jenkinsFormatter
    },
    'properties' : {
        "CheckoutDir" : StringProperty,
        "Platform" : StringProperty
    },
    'state' : {
        "PathFmt" : PathFmtState
    }
}
