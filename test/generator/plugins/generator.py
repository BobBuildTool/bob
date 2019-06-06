
from bob.generators.VisualStudio import vs2019ProjectGenerator

def g1(package, argv, extra):
    print("PLUGIN:", package.getName())
    print("PLUGIN:", argv)
    print("PLUGIN:", extra)

manifest = {
    'apiVersion' : "0.3",
    'projectGenerators' : {
        "g1" : g1,
        "__vs2019" : vs2019ProjectGenerator,
    }
}
