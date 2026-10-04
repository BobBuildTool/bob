def doCommand(packages, argv, bobRoot):
    return 0

manifest = {
    'apiVersion' : "1.2.1.dev1",
    'commands' : {
        'hello' : {
            'func' : doCommand,
            'help' : "Example plugin command",
        },
        'another-command' : {
            'func' : doCommand,
            'help' : "Command with a rather long name",
        },
        'nohelp' : {
            'func' : doCommand,
        },
        'bare' : doCommand,
    },
}
