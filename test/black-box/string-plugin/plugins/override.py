from bob.errors import ParseError

# $(override,BUILD_TYPE_OVERRIDE,default)
# expected format is "package_a:override1,package_b:override2,..."
def override(args, env, recipe, **options):
    if len(args) != 2:
        raise ParseError("override expects two arguments")

    if args[0] in env:
        o = env[args[0]]
        o = [ i.split(":") for i in o.split(",") ]
        o = { k:v for (k,v) in o }
        return o.get(recipe.getPackageName(), args[1])
    else:
        return args[1]

manifest = {
    'apiVersion' : "0.24rc1",
    'stringFunctions' : {
        "override" : override
    }
}
