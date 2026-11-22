#!/usr/bin/env python3

from textwrap import dedent
import lzma
import os, os.path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

bob = ["bob"]

GITEA_VERSION = "1.27.3"
GITEA_HOST = "127.0.0.1"
GITEA_PORT = 3000

GITEA_ADMIN_USER = "bob"
GITEA_ADMIN_PASSWORD = "test-password-123"
GITEA_ADMIN_EMAIL = "bob@bob.test"
GITEA_TOKEN_NAME = "bob-integration-test"
GITEA_TOKEN_SCOPES = "read:package,write:package"

# The generic package registry stores packages below an owner (user or
# organization). Simply reuse the admin user created below as owner so no
# organization needs to be created first.
GITEA_OWNER = GITEA_ADMIN_USER


def download(url, dest):
    if os.path.exists(dest): return
    print("[TEST]:", "Download", url)
    # dl.gitea.com rejects the default urllib user agent as a bot.
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req) as response:
            with open(tmp, "wb") as f:
                shutil.copyfileobj(response, f)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def prepare(d, archiveUrl):
    """(Re-)create the project configuration of a test directory.

    Resets any left over state from a previous run and (re-)creates the
    'config.yaml' and 'default.yaml' that hold the general bob settings
    and the Gitea archive configuration, respectively. Every test
    directory gets its own package name so that the tests don't see each
    other's artifacts.
    """
    for i in os.listdir(d):
        if i.startswith('.bob-'):
            os.remove(os.path.join(d, i))

    with open(os.path.join(d, "config.yaml"), "w") as f:
        print('bobMinimumVersion: "0.24"', file=f)
        print('scriptLanguage: bash', file=f)

    with open(os.path.join(d, "default.yaml"), "w") as f:
        print('archive:', file=f)
        print('    -', file=f)
        print('        backend: gitea', file=f)
        print('        url: "{}"'.format(archiveUrl), file=f)
        print('        owner: "{}"'.format(GITEA_OWNER), file=f)
        print('        package: "{}"'.format(d), file=f)
        print('        flags: [download, upload, managed]', file=f)


def assertEqual(a, b):
    if a != b:
        print("[TEST]:", "Error: '{}' != '{}'".format(a, b))
        sys.exit(3)


def runBob(args, cwd, env):
    print("[TEST]:", "Run", " ".join(args), "in", cwd)
    res = subprocess.run(bob + args, cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(res.stdout.decode(errors="replace"))
    return res


def readResult(d, package):
    path = os.path.join(d, "work", package, "dist", "1", "workspace", "result.txt")
    with open(path) as f:
        return f.read()


###############################################################################

def testUploadDownload(archiveUrl, env):
    """Build a package, upload it and download it again from Gitea.

    The workspace is wiped between the build and the download so that the
    downloaded content is actually verified to come from the archive.
    """
    d = "testUploadDownload"
    prepare(d, archiveUrl)

    res = runBob(["build", "--no-sandbox", "--download=no", "--upload", "root"], d, env)
    assertEqual(res.returncode, 0)
    assertEqual(readResult(d, "root"), "ok")

    shutil.rmtree(os.path.join(d, "work"))

    res = runBob(["build", "--no-sandbox", "--download=forced", "root"], d, env)
    assertEqual(res.returncode, 0)
    assertEqual(readResult(d, "root"), "ok")


def testManage(archiveUrl, env):
    """Exercise the 'bob archive' management commands against Gitea.

    Two artifacts are uploaded, one of them is pruned via 'bob archive
    clean' and it is verified that only the pruned artifact became
    unavailable for download afterwards.
    """
    d = "testManage"
    prepare(d, archiveUrl)

    res = runBob(["build", "--no-sandbox", "--download=no", "--upload", "root-alpha", "root-bravo"], d, env)
    assertEqual(res.returncode, 0)

    shutil.rmtree(os.path.join(d, "work"))

    res = runBob(["archive", "scan", "-v"], d, env)
    assertEqual(res.returncode, 0)

    res = runBob(["archive", "find", 'metaEnv.TYPE == "alpha"'], d, env)
    assertEqual(res.returncode, 0)
    if not res.stdout.strip():
        print("[TEST]:", "Error: could not find uploaded 'alpha' artifact!")
        sys.exit(3)

    res = runBob(["archive", "clean", "-v", 'metaEnv.TYPE == "alpha"'], d, env)
    assertEqual(res.returncode, 0)

    # The retained artifact must still be downloadable...
    res = runBob(["build", "--no-sandbox", "--download=forced", "root-alpha"], d, env)
    assertEqual(res.returncode, 0)
    assertEqual(readResult(d, "root-alpha"), "alpha")

    # ...but the pruned one must be gone from the archive.
    res = runBob(["build", "--no-sandbox", "--download=forced", "root-bravo"], d, env)
    if res.returncode == 0:
        print("[TEST]:", "Error: pruned artifact could still be downloaded!")
        sys.exit(3)


TESTS = (
    ("Upload and download artifacts", testUploadDownload),
    ("Manage artifacts (scan/find/clean)", testManage),
)

###############################################################################

# Only works on Linux for the time being...
if sys.platform != "linux":
    sys.exit(240)

CACHE = "cache"
GITEA_XZ_FILE = os.path.join(CACHE, "gitea-{}-linux-amd64.xz".format(GITEA_VERSION))
GITEA_FILE = os.path.join(CACHE, "gitea")
GITEA_DOWNLOAD_URL = "https://dl.gitea.com/gitea/{0}/gitea-{0}-linux-amd64.xz".format(GITEA_VERSION)

os.makedirs(CACHE, exist_ok=True)

# Download the xz compressed, self contained, single file Gitea binary
# unless it is already cached from a previous run.
download(GITEA_DOWNLOAD_URL, GITEA_XZ_FILE)

if not os.path.exists(GITEA_FILE):
    print("[TEST]:", "Extracting Gitea...")
    tmp = GITEA_FILE + ".part"
    try:
        with lzma.open(GITEA_XZ_FILE) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp, GITEA_FILE)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

st = os.stat(GITEA_FILE)
os.chmod(GITEA_FILE, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

gitea = os.path.abspath(GITEA_FILE)

# Run Gitea self-contained (sqlite, no mailer, no SSH,...) in a throw-away
# work directory and execute the tests against it.
with tempfile.TemporaryDirectory() as workDir:
    appIni = os.path.join(workDir, "app.ini")

    def giteaCmd(args):
        return subprocess.run([gitea, "--config", appIni, "--work-path", workDir] + args,
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    secretKey = subprocess.run([gitea, "generate", "secret", "SECRET_KEY"],
                                check=True, stdout=subprocess.PIPE).stdout.decode().strip()
    internalToken = subprocess.run([gitea, "generate", "secret", "INTERNAL_TOKEN"],
                                    check=True, stdout=subprocess.PIPE).stdout.decode().strip()

    with open(appIni, "w") as f:
        print(dedent(f"""\
            APP_NAME = Bob integration test
            RUN_MODE = prod

            [security]
            INSTALL_LOCK = true
            SECRET_KEY = {secretKey}
            INTERNAL_TOKEN = {internalToken}

            [server]
            PROTOCOL = http
            DOMAIN = {GITEA_HOST}
            HTTP_ADDR = {GITEA_HOST}
            HTTP_PORT = {GITEA_PORT}
            ROOT_URL = http://{GITEA_HOST}:{GITEA_PORT}/
            DISABLE_SSH = true
            OFFLINE_MODE = true
            SSH_CREATE_AUTHORIZED_KEYS_FILE = false

            [database]
            DB_TYPE = sqlite3

            [service]
            DISABLE_REGISTRATION = true

            [packages]
            ENABLED = true

            [session]
            PROVIDER = memory

            [cache]
            ENABLED = false

            [mailer]
            ENABLED = false

            [log]
            MODE = console
            LEVEL = warn
            """), file=f)

    print("[TEST]:", "Initializing Gitea database...")
    giteaCmd(["migrate"])

    print("[TEST]:", "Creating admin user...")
    giteaCmd(["admin", "user", "create",
              "--username", GITEA_ADMIN_USER,
              "--password", GITEA_ADMIN_PASSWORD,
              "--email", GITEA_ADMIN_EMAIL,
              "--admin", "--must-change-password=false"])

    print("[TEST]:", "Generating access token...")
    token = giteaCmd(["admin", "user", "generate-access-token",
                      "--username", GITEA_ADMIN_USER,
                      "--token-name", GITEA_TOKEN_NAME,
                      "--scopes", GITEA_TOKEN_SCOPES,
                      "--raw"]).stdout.decode().strip()

    archiveUrl = "http://{}:{}@{}:{}".format(GITEA_ADMIN_USER, token, GITEA_HOST, GITEA_PORT)

    # Give bob a pristine environment. In particular the user's own
    # ~/.config/bob/default.yaml must not interfere (e.g. by prepending
    # another archive or running build hooks).
    env = os.environ.copy()
    tmpConfigHome = tempfile.mkdtemp()
    env["XDG_CONFIG_HOME"] = tmpConfigHome

    try:
        giteaLog = open(os.path.join(workDir, "gitea.log"), "wb")
        giteaProc = subprocess.Popen([gitea, "--config", appIni, "--work-path", workDir, "web"])
        print("[TEST]:", "Gitea running as pid", giteaProc.pid, "in", workDir)

        print("[TEST]:", "Waiting for Gitea to get ready...")
        for i in range(60):
            time.sleep(1)
            if giteaProc.poll() is not None:
                print("[TEST]:", "Gitea terminated prematurely!")
                sys.exit(2)
            try:
                with urllib.request.urlopen(
                        "http://{}:{}/api/healthz".format(GITEA_HOST, GITEA_PORT)) as r:
                    if r.status == 200: break
            except (urllib.error.URLError, OSError):
                pass
        else:
            print("[TEST]:", "Timeout waiting for Gitea!")
            sys.exit(1)

        # Execute tests
        for name, test in TESTS:
            print("[TEST]:", "Execute", name, "...")
            test(archiveUrl, env)

    finally:
        if giteaProc.poll() is None:
            print("[TEST]:", "Shutting down Gitea...")
            giteaProc.terminate()
            giteaProc.wait(10)
        shutil.rmtree(tmpConfigHome, ignore_errors=True)
