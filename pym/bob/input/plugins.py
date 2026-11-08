# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Plugin API: properties, states and settings."""

from ..utils import updateDicRecursive
import copy
import schema


class PluginProperty:
    """Base class for plugin property handlers.

    A plugin should sub-class this class to parse custom properties in a
    recipe. For each recipe an object of that class is created then. The
    default constructor just stores the *present* and *value* parameters as
    attributes in the object.

    :param bool present: True if property is present in recipe
    :param value: Unmodified value of property from recipe or None if not present.
    """

    def __init__(self, present, value):
        self.present = present
        self.value = value

    def inherit(self, cls):
        """Inherit from a class.

        The default implementation will use the value from the class if the
        property was not present. Otherwise the class value will be ignored.

        :param cls: The property instance of the class
        :type cls: PluginProperty
        """
        if not self.present:
            self.present = cls.present
            self.value = cls.value

    def isPresent(self):
        """Return True if the property was present in the recipe."""
        return self.present

    def getValue(self):
        """Get (parsed) value of the property."""
        return self.value

    @staticmethod
    def validate(data):
        """Validate type of property.

        Ususally the plugin will reimplement this static method and return True
        only if *data* has the expected type. The default implementation will
        always return True.

        :param data: Parsed property data from the recipe
        :return: True if data has expected type, otherwise False.
        """
        return True

class PluginState:
    """Base class for plugin state trackers.

    State trackers are used by plugins to compute the value of one or more
    properties as the dependency tree of all recipes is traversed.

    .. attention::
        Objects of this class are tested for equivalence. The default
        implementation compares all members of the involved objects. If custom
        types are stored in the object you have to provide a suitable
        ``__eq__`` and ``__ne__`` implementation because Python falls back to
        object identity which might not be correct.  If these operators are not
        working correctly then Bob may slow down considerably.
    """

    def __eq__(self, other):
        return vars(self) == vars(other)

    def __ne__(self, other):
        return vars(self) != vars(other)

    def copy(self):
        """Return a copy of the object.

        The default implementation uses copy.deepcopy() which should usually be
        enough. If the plugin uses a sophisticated state tracker, especially
        when holding references to created packages, it might be usefull to
        provide a specialized implementation.
        """
        return copy.deepcopy(self)

    def onEnter(self, env, properties):
        """Begin creation of a package.

        The state tracker is about to witness the creation of a package. The passed
        environment, tools and (custom) properties are in their initial state that
        was inherited from the parent recipe.

        :param env: Complete environment
        :type env: Mapping[str, str]
        :param properties: All custom properties
        :type properties: Mapping[str, :class:`bob.input.PluginProperty`]
        """
        pass

    def onUse(self, downstream):
        """Use provided state of downstream package.

        This method is called if the user added the name of the state tracker
        to the ``use`` clause in the recipe. A state tracker supporting this
        notion should somehow pick up and merge the state of the downstream
        package.

        The default implementation does nothing.

        :param bob.input.PluginState downstream: State of downstream package
        """
        pass

    def onSkip(self, downstream):
        """Do not use provided state of downstream package.

        This method is called if the name of the state tracker is missing in
        the ``use`` clause of the dependency. The default implementation does
        nothing.

        :param bob.input.PluginState downstream: State of downstream package
        """
        pass

    def onFinish(self, env, properties):
        """Finish creation of a package.

        The package was computed and is about to be created. The passed *env*
        and *properties* have their final state after all downstream
        dependencies have been resolved. The plugin may modify the *env* and
        *properties* to make final adjustments.

        :param env: Complete environment
        :type env: Mapping[str, str]
        :param properties: All custom properties
        :type properties: Mapping[str, :class:`bob.input.PluginProperty`]
        """
        pass

class PluginSetting:
    priority = 50

    """Base class for plugin settings.

    Plugins can be configured in the user configuration of a project. The
    plugin must derive from this class, create an object with the default value
    and assign it to 'settings' in the plugin manifest. The default
    constructor will just store the passed value in the ``settings`` member.

    :param settings: The default settings
    """

    def __init__(self, settings):
        self.settings = settings

    def merge(self, other):
        """Merge other settings into current ones.

        This method is called when other configuration files with a higher
        precedence have been parsed. The settings in these files are first
        validated by invoking the ``validate`` static method. Then this method
        is called that should update the current object with the value of
        *other*.

        The default implementation implements the following policy:

        * Dictionaries are merged recursively on a key-by-key basis
        * Lists are appended to each other
        * Everything else in *other* replaces the current settings

        It is assumed that the actual settings are stored in the ``settings``
        member variable.

        :param other: Other settings with higher precedence
        """
        if isinstance(self.settings, dict) and isinstance(other, dict):
            self.settings = updateDicRecursive(self.settings, other)
        elif isinstance(self.settings, list) and isinstance(other, list):
            self.settings = self.settings + other
        else:
            self.settings = other

    def getSettings(self):
        """Getter for settings data."""
        return self.settings

    @staticmethod
    def validate(data):
        """Validate type of settings.

        Ususally the plugin will reimplement this method and return True only
        if *data* has the expected type. The default implementation will always
        return True.

        :param data: Parsed settings data from user configuration
        :return: True if data has expected type, otherwise False.
        """
        return True

class SentinelSetting(PluginSetting):
    """Sentinel for "include" and "require" settings"""

    def __init__(self):
        pass

    def merge(self, other):
        pass

class BuiltinSetting(PluginSetting):
    """Tiny wrapper to define Bob built-in settings"""

    def __init__(self, schema, updater, mangle = False, priority=50):
        self.__schema = schema
        self.__updater = updater
        self.__mangle = mangle
        self.priority = priority

    def merge(self, other):
        self.__updater(self.__schema.validate(other) if self.__mangle else other)

    def validate(self, data):
        self.__schema.validate(data)
        return True

def isPluginPropertyClass(cls):
    return isinstance(cls, type) and issubclass(cls, PluginProperty)

def isPluginStateClass(cls):
    return isinstance(cls, type) and issubclass(cls, PluginState)
