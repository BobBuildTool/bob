
def g1(package, argv, extra, bobRoot):
    print("PLUGIN:", package.getName())
    print("PLUGIN:", argv)
    print("PLUGIN:", extra)

manifest = {
    'apiVersion' : "0.24rc1",
    'projectGenerators' : {
        "g1" : g1,
    }
}
