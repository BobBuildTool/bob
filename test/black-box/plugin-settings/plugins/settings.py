from bob.input import PluginSetting

class Settings(PluginSetting):
    pass

pluginSetting = Settings("default")

def getSettings(args, **options):
    return pluginSetting.getSettings()

manifest = {
    'apiVersion' : "0.15",
    'stringFunctions' : {
        "get-settings" : getSettings,
    },
    'settings' : {
        "Settings" : pluginSetting,
    },
}
