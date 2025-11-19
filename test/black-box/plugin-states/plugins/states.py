from bob.input import PluginState

class ChildrenState(PluginState):
    """Keep track of used/skipped children in dependency list"""

    def __init__(self):
        self.used = 0
        self.skipped = 0

    def onUse(self, downstream):
        self.used += 1

    def onSkip(self, downstream):
        self.skipped += 1

    @property
    def num(self):
        return self.used + self.skipped

def usedChildren(args, states, **kwargs):
    return str(states['CountChildren'].used)

def skippedChildren(args, states, **kwargs):
    return str(states['CountChildren'].skipped)

def numChildren(args, states, **kwargs):
    return str(states['CountChildren'].num)

manifest = {
    'apiVersion' : "1.1",
    'state' : {
        "CountChildren" : ChildrenState
    },
    'stringFunctions' : {
        "usedChildren" : usedChildren,
        "skippedChildren" : skippedChildren,
        "numChildren" : numChildren,
    },
}
