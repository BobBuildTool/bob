# Sample plugin that adds a new top level command: 'bob hello'
#
# Run e.g. 'bob hello root' to print the path of all matching packages.

import argparse

def doHello(packages, argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob hello",
        description="Print the path of all matching packages.")
    parser.add_argument('package', nargs='?', default="",
        help="Package to query (defaults to all root packages)")
    args = parser.parse_args(argv)

    for (stack, node) in packages.queryTreePath(args.package):
        print("/".join(stack) if stack else "/")

manifest = {
    'apiVersion' : "1.2.1.dev1",
    'commands' : {
        'hello' : {
            'func' : doHello,
            'help' : "Print all package paths",
        },
    },
}
