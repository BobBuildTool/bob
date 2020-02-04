
def g1(package, argv, extra):
    print("PLUGIN:", package.getName())
    print("PLUGIN:", argv)
    print("PLUGIN:", extra)

manifest = {
    'apiVersion' : "0.3",
    'projectGenerators' : {
        "g1" : g1,
    }
}
