from bob.errors import ParseError

# Sample string function that may be called as follows:
#
#   $(override,FOOBAR,default)
#
# The function reads variable FOOBAR of the environment. The expected format of
# the content is "package_a:override1,package_b:override2,...". If the variable
# is not set or the current package does not match anything then "default" is
# returned. Otherwise the matching override from FOOBAR is returned.
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
    'apiVersion' : "0.2",
    'stringFunctions' : {
        "override" : override
    }
}
