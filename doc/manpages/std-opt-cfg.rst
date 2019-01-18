``-c CONFIGFILE``
    Use additional user configuration file. May be given more than once.

    The configuration files have the same syntax as ``default.yaml``. Their
    settings have higher precedence than ``default.yaml``, with the last given
    configuration file being the highest.

``-D VAR[=VALUE]``
    Override default environment variable. May be given more than once. If the
    optional ``VALUE`` is not supplied the variable will be defined to an empty
    string.

