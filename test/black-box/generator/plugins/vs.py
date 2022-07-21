
from bob.generators.VisualStudio import vs2019ProjectGenerator

# Re-import Visual Studio generator just for the test sake. It is not exposed
# on non-Windows platforms otherwise.
manifest = {
    'apiVersion' : "0.16.1.dev33",
    'projectGenerators' : {
        "__vs2019" : vs2019ProjectGenerator,
    }
}
