from bob.input import PluginProperty

class Property(PluginProperty):
    pass

manifest = {
    'apiVersion' : "0.19",
    'properties' : {
        "Property" : Property,
    },
}
