from binascii import hexlify
from hashlib import sha1

def asHex(b):
    return hexlify(b).decode("ascii")

def dumpSteps(step, f):
    vid = step.getVariantId()

    print("/".join(step.getPackage().getStack()), step.getLabel(), asHex(vid), file=f)
    script = step.getDigestScript()
    if script:
        print("  digestScript:", asHex(sha1(script.encode('utf8')).digest()), file=f)
    sandbox = step.getSandbox()
    if sandbox:
        print("  sandbox:", asHex(sandbox.getStep().getVariantId()), file=f)
    tools = sorted(step.getTools().items())
    if tools:
        print("  tools:", file=f)
        for (name, tool) in tools:
            print("   ", name, tool.getPath(), asHex(tool.getStep().getVariantId()), file=f)
    env = sorted(step.getEnv().items())
    if env:
        print("  env:", file=f)
        for (name, val) in env:
            print("   ", "{}={}".format(name, val), file=f)
    args = step.getArguments()
    if args:
        print("  args:", file=f)
        for arg in args:
            print("   ", asHex(arg.getVariantId()), file=f)
    for d in step.getAllDepSteps():
        dumpSteps(d, f)

def dumper(package, argv, extra):
    with open(argv[0], "w") as f:
        dumpSteps(package.getPackageStep(), f)

manifest = {
    'apiVersion' : "0.3",
    'projectGenerators' : {
        'dumper' : dumper,
    }
}
