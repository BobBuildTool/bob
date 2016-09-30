
def g1(package, argv, extra):
    print(package.getName())

manifest = {
    'apiVersion' : "0.3",
    'projectGenerators' : {
        "g1" : g1
    }
}
