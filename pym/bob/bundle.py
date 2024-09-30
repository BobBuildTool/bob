# Bob build tool
# Copyright (C) 2024 Secunet Security Networks AG
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import BuildError
from .tty import stepExec, EXECUTED
from .utils import hashFile

import asyncio
import concurrent.futures
import fnmatch
import gzip
import hashlib
import os
import schema
import signal
import tarfile
import tempfile
import yaml

class Bundler:
    def __init__(self, name, excludes):
        self.__name = name
        self.__bundleFile = os.path.join(os.getcwd(), self.__name) + ".tar"
        self.__excludes = excludes
        self.__tempDir = tempfile.TemporaryDirectory()
        self.__tempDirPath = os.path.join(self.__tempDir.name, self.__name)
        self.__bundled = {}

        if os.path.exists(self.__bundleFile):
            raise BuildError(f"Bundle {self.__bundleFile} already exists!")
        os.mkdir(self.__tempDirPath)

    def _bundle(self, workspace, bundleFile):
        def reset(tarinfo):
            tarinfo.uid = tarinfo.gid = 0
            tarinfo.uname = tarinfo.gname = "root"
            tarinfo.mtime = 0
            return tarinfo

        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            files = []
            for root, dirs, filenames in os.walk(workspace):
                for f in filenames:
                    files.append(os.path.join(root, f))
            files.sort()
            with open(bundleFile, 'wb') as outfile:
                with gzip.GzipFile(fileobj=outfile, mode='wb', mtime=0) as zipfile:
                    with tarfile.open(fileobj=zipfile, mode="w:") as bundle:
                        for f in files:
                            bundle.add(f, arcname=os.path.relpath(f, workspace),
                                       recursive=False, filter=reset)
            digest = hashFile(bundleFile, hashlib.sha256).hex()

        except (tarfile.TarError, OSError) as e:
            raise BuildError("Cannot bundle workspace: " + str(e))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

        return ("ok", EXECUTED, digest)

    async def bundle(self, step, executor):
        for e in self.__excludes:
            if fnmatch.fnmatch(step.getPackage().getName(), e): return

        checkoutVariantId = step.getPackage().getCheckoutStep().getVariantId().hex()
        dest = os.path.join(self.__tempDirPath, step.getPackage().getRecipe().getName(),
                            checkoutVariantId)
        os.makedirs(dest)
        bundleFile = os.path.join(dest, "bundle.tgz")

        loop = asyncio.get_event_loop()
        with stepExec(step, "BUNDLE", "{}".format(step.getWorkspacePath())) as a:
            try:
                msg, kind, digest = await loop.run_in_executor(executor, Bundler._bundle,
                    self, step.getWorkspacePath(), bundleFile)
                a.setResult(msg, kind)
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError("Upload of bundling interrupted.")

        self.__bundled[checkoutVariantId] = (step.getPackage().getRecipe().getName(), digest, bundleFile)

    def finalize(self):
        bundle = []
        with tarfile.open(self.__bundleFile, "w") as bundle_tar:

            for vid, (package, digest, bundleFile) in sorted(self.__bundled.items()):
                bundle.append({vid : {"digestSHA256" : digest,
                                "name" : package}})
                print(f"add to bundle: {bundleFile}")
                bundle_tar.add(bundleFile,
                              arcname=os.path.relpath(bundleFile, self.__tempDir.name))

            bundleConfig = self.__name + ".yaml"
            bundleConfigPath = os.path.join(self.__tempDirPath, bundleConfig)
            with open(bundleConfigPath, "w") as f:
                yaml.dump(bundle, f, default_flow_style=False)
            bundle_tar.add(bundleConfigPath, arcname=os.path.join(self.__name, bundleConfig))

class Unbundler:
    BUNDLE_SCHEMA = schema.Schema([{
      str : schema.Schema({
                "name" : str,
                "digestSHA256" : str
            })
    }])

    def __init__(self, bundles):
        self.__bundles = bundles

    def getFromBundle(self, variantId):
        for bundleFile, items in self.__bundles.items():
            for b in items:
                if variantId.hex() in b:
                    data = b.get(variantId.hex())
                    return (bundleFile, os.path.join(os.path.dirname(bundleFile), data['name'], variantId.hex(),
                                         "bundle.tgz"), data['digestSHA256'])
        return None

