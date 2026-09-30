# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Schema validators for recipe and configuration files."""

from ..stringparser import IfExpression
import re
import schema


class ScmValidator:
    def __init__(self, scmSpecs):
        self.__scmSpecs = scmSpecs

    def __validateScm(self, scm):
        if 'scm' not in scm:
            raise schema.SchemaMissingKeyError("Missing 'scm' key in {}".format(scm), None)
        if scm['scm'] not in self.__scmSpecs.keys():
            raise schema.SchemaWrongKeyError('Invalid SCM: {}'.format(scm['scm']), None)
        return self.__scmSpecs[scm['scm']].validate(scm)

    def validate(self, data):
        if isinstance(data, dict):
            data = [self.__validateScm(data)]
        elif isinstance(data, list):
            data = [ self.__validateScm(i) for i in data ]
        else:
            raise schema.SchemaUnexpectedTypeError(
                'checkoutSCM must be a SCM spec or a list threreof',
                None)
        return data

class KeyValDefineValidator:
    VAR_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

    def __init__(self, keyword, conditional=True):
        self.__keyword = keyword
        self.__conditional = conditional

    def validate(self, data):
        if not isinstance(data, dict):
            raise schema.SchemaUnexpectedTypeError(
                "{}: must be a dictionary".format(self.__keyword), None)

        data = data.copy()
        for key,value in sorted(data.items()):
            if not isinstance(key, str):
                raise schema.SchemaUnexpectedTypeError(
                    f"{self.__keyword}: bad variable '{key}'. Variable names must be strings!",
                    None)
            if key.startswith("BOB_"):
                raise schema.SchemaWrongKeyError(
                    f"{self.__keyword}: bad variable '{key}'. Variables starting with 'BOB_' are reserved!",
                    None)
            if self.VAR_NAME.match(key) is None:
                raise schema.SchemaWrongKeyError(
                    f"{self.__keyword}: bad variable name '{key}'.",
                    None)
            if isinstance(value, dict) and self.__conditional:
                self.VAR_DEF.validate(value, error=f"{self.__keyword}: {key}: invalid definition!")
                data[key] = self._convertItemDict(value)
            elif isinstance(value, str):
                if self.__conditional:
                    data[key] = self._convertItemStr(value)
            else:
                raise schema.SchemaUnexpectedTypeError(
                    f"{self.__keyword}: {key}: bad variable definition type.",
                    None)
        return data

class VarDefineValidator(KeyValDefineValidator):
    VAR_DEF = schema.Schema({
            'value' : str,
            schema.Optional("if"): schema.Or(str, IfExpression),
        })

    def _convertItemDict(self, value):
        return (value['value'], value.get('if'))

    def _convertItemStr(self, value):
        return (value, None)

class AuditFile:
    __slots__ = ('filename', 'condition', 'encoding')

    def __init__(self, filename, condition=None, encoding="utf-8"):
        self.filename = filename
        self.condition = condition
        self.encoding = encoding

    def substitute(self, env):
        return (env.substitute(self.filename, "filename"),
                env.substitute(self.encoding, "encoding"))

class AuditFilesValidator(KeyValDefineValidator):
    VAR_DEF = schema.Schema({
            'filename' : str,
            schema.Optional("if"): schema.Or(str, IfExpression),
            schema.Optional("encoding") : str,
        })

    def _convertItemDict(self, value):
        return AuditFile(value['filename'], value.get('if'), value.get('encoding', "utf-8"))

    def _convertItemStr(self, value):
        return AuditFile(value)

class HttpUrlValidator:
    def validate(self, data):
        if not isinstance(data, str):
            raise schema.SchemaError(None, "Url field must be a string!")

        import urllib.parse
        import urllib.error
        try:
            u = urllib.parse.urlparse(data)
            if u.username is not None and u.password is None:
                raise schema.SchemaError(None, "Url with username but without password not supported!")
            if u.scheme not in ('http', 'https'):
                raise schema.SchemaError(None, "Unsupported URL schema: " + u.scheme)
        except urllib.error.URLError as e:
            raise schema.SchemaError(None, "Unparsable URL: " + str(e))

        return data

class ArchiveValidator:
    def __init__(self):
        self.__validTypes = schema.Schema({'backend': schema.Or('none', 'file', 'http', 'shell', 'azure', 'gitea')},
            ignore_extra_keys=True)
        baseArchive = {
            'backend' : str,
            schema.Optional('name') : str,
            schema.Optional('flags') : schema.Schema(["download", "upload", "managed",
                "nofail", "nolocal", "nojenkins", "cache", "strictdownload"])
        }
        fileArchive = baseArchive.copy()
        fileArchive["path"] = str
        fileArchive[schema.Optional("fileMode")] = int
        fileArchive[schema.Optional("directoryMode")] = int
        httpArchive = baseArchive.copy()
        httpArchive["url"] = HttpUrlValidator()
        httpArchive[schema.Optional("sslVerify")] = bool
        httpArchive[schema.Optional("retries")] = PositiveValidator()
        shellArchive = baseArchive.copy()
        shellArchive.update({
            schema.Optional('download') : str,
            schema.Optional('upload') : str,
        })
        azureArchive = baseArchive.copy()
        azureArchive.update({
            'account' : str,
            'container' : str,
            schema.Optional('key') : str,
            schema.Optional('sasToken"') : str,
        })
        giteaArchive = baseArchive.copy()
        giteaArchive.update({
            'url' : HttpUrlValidator(),
            'owner' : str,
            'package' : str,
            schema.Optional('sslVerify') : bool,
            schema.Optional('retries') : PositiveValidator(),
        })
        self.__backends = {
            'none' : schema.Schema(baseArchive),
            'file' : schema.Schema(fileArchive),
            'http' : schema.Schema(httpArchive),
            'shell' : schema.Schema(shellArchive),
            'azure' : schema.Schema(azureArchive),
            'gitea' : schema.Schema(giteaArchive),
        }

    def validate(self, data):
        if isinstance(data, dict):
            self.__validTypes.validate(data)
            return [ self.__backends[data['backend']].validate(data) ]
        elif isinstance(data, list):
            return [ self.__validTypes.validate(i) and self.__backends[i['backend']].validate(i)
                     for i in data ]
        else:
            raise schema.SchemaError(None, "Invalid archive specification!")

class MountValidator:
    def __init__(self):
        self.__options = schema.Schema(
            ["nolocal", "nojenkins", "nofail", "rw"],
            error="Invalid mount option specified!")

    def validate(self, data):
        if isinstance(data, str):
            return (data, data, [])
        elif isinstance(data, list) and (len(data) in [2, 3]):
            if not isinstance(data[0], str):
                raise schema.SchemaError(None, "Expected string as first mount argument!")
            if not isinstance(data[1], str):
                raise schema.SchemaError(None, "Expected string as second mount argument!")
            if len(data) == 3:
                self.__options.validate(data[2])
                return tuple(data)
            else:
                return (data[0], data[1], [])

        raise schema.SchemaError(None, "Mount entry must be a string or a two/three items list!")

class ToolValidator:
    def __init__(self, toolNameSchema):
        self.__schema = schema.Schema({
            "name" : toolNameSchema,
            schema.Optional("if"): schema.Or(str, IfExpression),
        })

    def validate(self, data):
        if isinstance(data, str):
            return (data, None)
        else:
            self.__schema.validate(data)
            return (data["name"], data.get("if"))

class PositiveValidator:
    def validate(self, data):
        if not isinstance(data, int):
            raise schema.SchemaError(None, "Value must be an integer")
        if data < 0:
            raise schema.SchemaError(None, "Int value must not be negative")
        return data

def wrapValidator(validator, message):
    def wrapper(data):
        if validator(data):
            return True
        else:
            raise schema.SchemaError(message)
    return wrapper

class StrDictValidator:
    """Validate a dict that has only plain string keys.

    Behaves like a plain schema dict whose keys are strings or
    ``schema.Optional`` strings. The schema library tries every schema key for
    every key of the data, raising and formatting an exception on each
    mismatch, which is very slow for large specs like the recipe schema.
    Instead, the keys are looked up directly here. The value schemas are still
    validated by the schema library.

    The spec is compiled lazily on first use so that recursive specs can be
    completed after the validator was created.
    """

    def __init__(self, spec):
        self.__spec = spec
        self.__keys = None

    def __repr__(self):
        return "Schema({!r})".format(self.__spec)

    def __compile(self):
        keys = {}
        required = set()
        defaults = []
        for key, value in self.__spec.items():
            if isinstance(key, schema.Optional):
                name = key.schema
                if hasattr(key, "default"):
                    defaults.append((name, key.default))
            else:
                name = key
                required.add(name)
            if not isinstance(name, str):
                raise TypeError("StrDictValidator only supports string keys: " + repr(key))
            keys[name] = schema.Schema(value)
        self.__required = required
        self.__defaults = defaults
        self.__keys = keys

    def validate(self, data, **kwargs):
        if self.__keys is None: self.__compile()
        if not isinstance(data, dict):
            raise schema.SchemaUnexpectedTypeError(
                "%r should be instance of 'dict'" % (data,), None)

        # Evaluate dictionaries last. Same order as the schema library.
        new = {}
        for key, value in sorted(data.items(), key=lambda i: isinstance(i[1], dict)):
            s = self.__keys.get(key) if isinstance(key, str) else None
            if s is None: continue
            try:
                new[key] = s.validate(value, **kwargs)
            except schema.SchemaError as x:
                raise schema.SchemaError(["Key '%s' error:" % key] + x.autos,
                                         [None] + x.errors)

        if not self.__required.issubset(new.keys()):
            missingKeys = self.__required - new.keys()
            raise schema.SchemaMissingKeyError("Missing key{}: {}".format(
                "s" if len(missingKeys) > 1 else "",
                ", ".join(repr(k) for k in sorted(missingKeys))), None)
        if len(new) != len(data):
            wrongKeys = [ k for k in data.keys() if k not in new ]
            raise schema.SchemaWrongKeyError("Wrong key{} {} in {!r}".format(
                "s" if len(wrongKeys) > 1 else "",
                ", ".join(repr(k) for k in sorted(wrongKeys, key=repr)), data), None)

        for name, default in self.__defaults:
            if name not in new:
                new[name] = default() if callable(default) else default

        return new
