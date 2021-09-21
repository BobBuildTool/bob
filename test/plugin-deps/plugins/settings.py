from bob.input import PluginSetting

class Settings(PluginSetting):
    pass

pluginSetting = Settings("default")

manifest = {
    'apiVersion' : "0.19",
    'settings' : {
        "Settings" : pluginSetting,
    },
}
