def doHello(packages, argv, bobRoot):
    path = argv[0] if argv else ""

    root = packages.getRootPackage()
    print("ROOT:", [s.getPackage().getName() for s in root.getDirectDepSteps()])

    print("ALIASES:", sorted(packages.getAliases()))

    for p in packages.queryPackagePath(path):
        print(f"PACKAGE: {p.getName()}"
              f" FOO={p.getPackageStep().getEnv().get('FOO', '<unset>')}"
              f" sandbox={p.getPackageStep().getSandbox() is not None}")

    print("ARGV:", argv)

manifest = {
    'apiVersion' : "1.2.1.dev1",
    'commands' : {
        'hello' : {
            'func' : doHello,
            'help' : "Example plugin command",
        },
    },
}
