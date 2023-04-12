def g2(packages, argv, extra, bobRoot):
    for p in packages:
        print("G2: ", p.getName())

manifest = {
    'apiVersion' : "0.22.1.dev24",
    'projectGenerators' : {
        "g2" : {
            "func" : g2,
            "query" : True
        }
    }
}
