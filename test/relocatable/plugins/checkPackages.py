from bob.errors import ParseError

# check if packages are relocatable or not

def _checkPackages(step):
    if step.isValid() and step.isPackageStep():
        if not step.isPackageRelocatable() and step.getPackage().getName() == 'some-tool-relocatable':
            raise ParseError('Package some-tool-relocatable should be relocatable!')
        if step.isPackageRelocatable() and step.getPackage().getName() == 'some-tool':
            raise ParseError('Package ' + step.getPackage().getName() + 'shouldn\'t be relocatable!')
    for s in step.getAllDepSteps():
        _checkPackages(s)

def checkPackages(package, argv, extra):
    _checkPackages(package.getPackageStep())

manifest = {
    'apiVersion' : "0.13",
    'projectGenerators' : {
        "checkPackages" : checkPackages
    }
}
