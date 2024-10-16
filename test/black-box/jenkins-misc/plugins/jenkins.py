from xml.etree import ElementTree

def munge(config, checkoutSteps, buildSteps, packageSteps, **kwargs):
    root = ElementTree.fromstring(config)
    if root.find("./properties/test.bob.canary") is None:
        ElementTree.SubElement(
            root.find("properties"),
            "test.bob.canary")
    return ElementTree.tostring(root, encoding="UTF-8")

manifest = {
    'apiVersion' : "0.20",
    'hooks' : {
        'jenkinsJobCreate' : munge,
        'jenkinsJobPostUpdate' : munge
    }
}
