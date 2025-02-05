// Copyright 2014 The Bazel Authors. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <mntent.h>
#include <pwd.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include "network-tools.h"
#include "process-tools.h"

#define PRINT_DEBUG(...)                                        \
  do {                                                          \
    if (global_debug) {                                         \
      fprintf(stderr, __FILE__ ":" S__LINE__ ": " __VA_ARGS__); \
    }                                                           \
  } while (0)

static bool global_debug = false;

// The uid and gid of the user and group 'nobody'.
static const int kNobodyUid = 65534;
static const int kNobodyGid = 65534;

// Options parsing result.
struct Options {
  const char *stdout_path;   // Where to redirect stdout (-l)
  const char *stderr_path;   // Where to redirect stderr (-L)
  char *const *args;         // Command to run (--)
  const char *sandbox_root;  // Sandbox root (-S)
  const char *working_dir;   // Working directory (-W)
  char **mount_sources;      // Map of directories to mount, from (-M)
  char **mount_targets;      // sources -> targets (-m)
  bool *mount_rw;            // mount rw (-w)
  size_t mount_map_sizes;    // How many elements in mount_{sources,targets}
  int num_mounts;            // How many mounts were specified
  char **create_dirs;        // empty dirs to create (-d)
  int num_create_dirs;       // How many empty dirs to create were specified
  uid_t uid;                 // User id in namespace
  uid_t gid;                 // Group id in namespace
  int create_netns;          // If 1, create a new network namespace.
  const char *host_name;     // Host name (-H)
};

// forward declaration
static int CreateTarget(const char *path, bool is_directory);

// Child function used by CheckNamespacesSupported() in call to clone().
static int CheckNamespacesSupportedChild(void *arg) { return 0; }

// Check whether the required namespaces are supported.
static int CheckNamespacesSupported() {
  const int stackSize = 1024 * 1024;
  char *stack;
  char *stackTop;
  pid_t pid;

  // Allocate stack for child.
  stack = malloc(stackSize);
  if (stack == NULL) {
    DIE("malloc failed\n");
  }

  // Assume stack grows downward.
  stackTop = stack + stackSize;

  // Create child with own namespaces. We use clone() instead of unshare() here
  // because of the kernel bug (ref. CreateNamespaces) that lets unshare fail
  // sometimes. As this check has to run as fast as possible, we can't afford to
  // spend time sleeping and retrying here until it eventually works (or not).
  CHECK_CALL(pid = clone(CheckNamespacesSupportedChild, stackTop,
                         CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWUTS |
                             CLONE_NEWIPC | CLONE_NEWNET | SIGCHLD,
                         NULL));
  CHECK_CALL(waitpid(pid, NULL, 0));

  return EXIT_SUCCESS;
}

// Print out a usage error. argc and argv are the argument counter and vector,
// fmt is a format,
// string for the error message to print.
static void Usage(int argc, char *const *argv, const char *fmt, ...) {
  int i;
  va_list ap;
  va_start(ap, fmt);
  vfprintf(stderr, fmt, ap);
  va_end(ap);

  fprintf(stderr, "\nUsage: %s [-S sandbox-root] -- command arg1\n", argv[0]);
  fprintf(stderr, "  provided:");
  for (i = 0; i < argc; i++) {
    fprintf(stderr, " %s", argv[i]);
  }
  fprintf(
      stderr,
      "\nMandatory arguments:\n"
      "  -S <sandbox-root>  directory which will become the root of the "
      "sandbox\n"
      "  --  command to run inside sandbox, followed by arguments\n"
      "\n"
      "Optional arguments:\n"
      "  -W <working-dir>  working directory\n"
      "  -d <dir>  create an empty directory in the sandbox\n"
      "  -M/-m <source/target>  system directory to mount inside the sandbox\n"
      "    Multiple directories can be specified and each of them will be "
      "mounted readonly.\n"
      "    The -M option specifies which directory to mount, the -m option "
      "specifies where to\n"
      "    mount it in the sandbox.\n"
      "  -n if set, a new network namespace will be created\n"
      "  -i if set, keep the uid/gid\n"
      "  -r if set, make the uid/gid be root, otherwise use nobody\n"
      "  -H <name> set host name\n"
      "  -D  if set, debug info will be printed\n"
      "  -l <file>  redirect stdout to a file\n"
      "  -L <file>  redirect stderr to a file\n"
      "  @FILE read newline-separated arguments from FILE\n");
  exit(EXIT_FAILURE);
}

// Deals with an unfinished (source but no target) mapping in opt.
// Also adds a new unfinished mapping if source is not NULL.
static void AddMountSource(char *source, struct Options *opt) {
  // The last -M flag wasn't followed by an -m flag, so assume that the source
  // should be mounted in the sandbox in the same path as outside.
  if (opt->mount_sources[opt->num_mounts] != NULL) {
    opt->mount_targets[opt->num_mounts] = opt->mount_sources[opt->num_mounts];
    opt->mount_rw[opt->num_mounts] = false;
    opt->num_mounts++;
  }
  if (source != NULL) {
    if (opt->num_mounts >= opt->mount_map_sizes - 1) {
      opt->mount_sources = realloc(opt->mount_sources,
                                   opt->mount_map_sizes * sizeof(char *) * 2);
      if (opt->mount_sources == NULL) {
        DIE("realloc failed\n");
      }
      memset(opt->mount_sources + opt->mount_map_sizes, 0,
             opt->mount_map_sizes * sizeof(char *));
      opt->mount_targets = realloc(opt->mount_targets,
                                   opt->mount_map_sizes * sizeof(char *) * 2);
      if (opt->mount_targets == NULL) {
        DIE("realloc failed\n");
      }
      memset(opt->mount_targets + opt->mount_map_sizes, 0,
             opt->mount_map_sizes * sizeof(char *));
      opt->mount_rw = realloc(opt->mount_rw,
                              opt->mount_map_sizes * sizeof(bool) * 2);
      if (opt->mount_rw == NULL) {
        DIE("realloc failed\n");
      }
      memset(opt->mount_rw + opt->mount_map_sizes, 0,
             opt->mount_map_sizes * sizeof(bool));
      opt->mount_map_sizes *= 2;
    }
    opt->mount_sources[opt->num_mounts] = source;
  }
}

static void ParseCommandLine(int argc, char *const *argv, struct Options *opt);

// Parses command line flags from a file named filename.
// Expects optind to be initialized to 0 before being called.
static void ParseOptionsFile(const char *filename, struct Options *opt) {
  FILE *const options_file = fopen(filename, "rb");
  if (options_file == NULL) {
    DIE("opening argument file %s failed\n", filename);
  }
  size_t sub_argv_size = 20;
  char **sub_argv = malloc(sizeof(char *) * sub_argv_size);
  sub_argv[0] = "";
  int sub_argc = 1;

  bool done = false;
  while (!done) {
    // This buffer determines the maximum size of arguments we can handle out of
    // the file. We DIE down below if it's ever too short.
    // 4096 is a common value for PATH_MAX. However, many filesystems support
    // arbitrarily long pathnames, so this might not be long enough to handle an
    // arbitrary filename no matter what. Twice the usual PATH_MAX seems
    // reasonable for now.
    char argument[8192];
    if (fgets(argument, sizeof(argument), options_file) == NULL) {
      if (feof(options_file)) {
        done = true;
        continue;
      } else {
        DIE("reading from argument file %s failed\n", filename);
      }
    }
    const size_t length = strlen(argument);
    if (length == 0) continue;
    if (length == sizeof(argument)) {
      DIE("argument from file %s is too long (> %zu)\n", filename,
          sizeof(argument));
    }
    if (argument[length - 1] == '\n') {
      argument[length - 1] = '\0';
    } else {
      done = true;
    }
    if (sub_argv_size == sub_argc + 1) {
      sub_argv_size *= 2;
      sub_argv = realloc(sub_argv, sizeof(char *) * sub_argv_size);
    }
    sub_argv[sub_argc++] = strdup(argument);
  }
  if (fclose(options_file) != 0) {
    DIE("closing options file %s failed\n", filename);
  }
  sub_argv[sub_argc] = NULL;

  ParseCommandLine(sub_argc, sub_argv, opt);
}

// Parse the command line flags and return the result in an Options structure
// passed as argument.
static void ParseCommandLine(int argc, char *const *argv, struct Options *opt) {
  extern char *optarg;
  extern int optind, optopt;
  int c;

  while ((c = getopt(argc, argv, ":CDd:il:L:m:M:nrS:W:w:H:")) != -1) {
    switch (c) {
      case 'C':
        // Shortcut for the "does this system support sandboxing" check.
        exit(CheckNamespacesSupported());
        break;
      case 'S':
        if (opt->sandbox_root == NULL) {
          char *sandbox_root = strdup(optarg);

          // Make sure that the sandbox_root path has no trailing slash.
          if (sandbox_root[strlen(sandbox_root) - 1] == '/') {
            sandbox_root[strlen(sandbox_root) - 1] = 0;
          }

          opt->sandbox_root = sandbox_root;
        } else {
          Usage(argc, argv,
                "Multiple sandbox roots (-S) specified, expected one.");
        }
        break;
      case 'W':
        if (opt->working_dir == NULL) {
          opt->working_dir = optarg;
        } else {
          Usage(argc, argv,
                "Multiple working directories (-W) specified, expected at most "
                "one.");
        }
        break;
      case 'd':
        if (optarg[0] != '/') {
          Usage(argc, argv,
                "The -d option must be used with absolute paths only.");
        }
        opt->create_dirs[opt->num_create_dirs++] = optarg;
        break;
      case 'i':
        opt->uid = getuid();
        opt->gid = getgid();
        break;
      case 'M':
        if (optarg[0] != '/') {
          Usage(argc, argv,
                "The -M option must be used with absolute paths only.");
        }
        AddMountSource(optarg, opt);
        break;
      case 'm':
        if (optarg[0] != '/') {
          Usage(argc, argv,
                "The -m option must be used with absolute paths only.");
        }
        if (opt->mount_sources[opt->num_mounts] == NULL) {
          Usage(argc, argv, "The -m option must be preceded by an -M option.");
        }
        opt->mount_rw[opt->num_mounts] = false;
        opt->mount_targets[opt->num_mounts] = optarg;
        opt->num_mounts++;
        break;
      case 'w':
        if (optarg[0] != '/') {
          Usage(argc, argv,
                "The -w option must be used with absolute paths only.");
        }
        if (opt->mount_sources[opt->num_mounts] == NULL) {
          Usage(argc, argv, "The -w option must be preceded by an -M option.");
        }
        opt->mount_rw[opt->num_mounts] = true;
        opt->mount_targets[opt->num_mounts] = optarg;
        opt->num_mounts++;
        break;
      case 'n':
        opt->create_netns = 1;
        break;
      case 'r':
        opt->uid = 0;
        opt->gid = 0;
        break;
      case 'H':
        opt->host_name = optarg;
        break;
      case 'D':
        global_debug = true;
        break;
      case 'l':
        if (opt->stdout_path == NULL) {
          opt->stdout_path = optarg;
        } else {
          Usage(argc, argv,
                "Cannot redirect stdout to more than one destination.");
        }
        break;
      case 'L':
        if (opt->stderr_path == NULL) {
          opt->stderr_path = optarg;
        } else {
          Usage(argc, argv,
                "Cannot redirect stderr to more than one destination.");
        }
        break;
      case '?':
        Usage(argc, argv, "Unrecognized argument: -%c (%d)", optopt, optind);
        break;
      case ':':
        Usage(argc, argv, "Flag -%c requires an argument", optopt);
        break;
    }
  }

  AddMountSource(NULL, opt);

  while (optind < argc && argv[optind][0] == '@') {
    const char *filename = argv[optind] + 1;
    const int old_optind = optind;
    optind = 0;
    ParseOptionsFile(filename, opt);
    optind = old_optind + 1;
  }

  if (argc > optind) {
    if (opt->args == NULL) {
      opt->args = argv + optind;
    } else {
      Usage(argc, argv, "Merging commands not supported.");
    }
  }
}

static void CreateNamespaces(int create_netns) {
  // This weird workaround is necessary due to unshare seldomly failing with
  // EINVAL due to a race condition in the Linux kernel (see
  // https://lkml.org/lkml/2015/7/28/833). An alternative would be to use
  // clone/waitpid instead.
  int delay = 1;
  int tries = 0;
  const int max_tries = 100;
  while (tries++ < max_tries) {
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWUTS | CLONE_NEWIPC |
                (create_netns ? CLONE_NEWNET : 0)) == 0) {
      PRINT_DEBUG("unshare succeeded after %d tries\n", tries);
      return;
    } else {
      if (errno != EINVAL) {
        perror("unshare");
        exit(EXIT_FAILURE);
      }
    }

    // Exponential back-off, but sleep at most 250ms.
    usleep(delay);
    if (delay < 250000) {
      delay *= 2;
    }
  }
  fprintf(stderr,
          "unshare failed with EINVAL even after %d tries, giving up.\n",
          tries);
  exit(EXIT_FAILURE);
}

static void CreateFile(const char *path) {
  int handle;
  CHECK_CALL_MSG(handle = open(path, O_CREAT | O_WRONLY | O_EXCL, 0666),
    "cannot create %s", path);
  CHECK_CALL(close(handle));
}

static void SetupDevices() {
  CHECK_CALL(mkdir("dev", 0755));
  const char *devs[] = {"/dev/null", "/dev/random", "/dev/urandom", "/dev/zero",
                        NULL};
  for (int i = 0; devs[i] != NULL; i++) {
    CreateFile(devs[i] + 1);
    CHECK_CALL(mount(devs[i], devs[i] + 1, NULL, MS_BIND, NULL));
  }

  // devtps mount with ptmx symlink for pseudoterminals
  CreateTarget("dev/pts", true);
  CHECK_CALL(mount("devpts", "dev/pts", "devpts", MS_NOSUID | MS_NOEXEC, "ptmxmode=0666"));
  CHECK_CALL(symlink("pts/ptmx", "dev/ptmx"));

  CreateTarget("dev/shm", true);
  CHECK_CALL(mount("tmpfs", "dev/shm", "tmpfs", MS_NOSUID | MS_NODEV, NULL));

  CHECK_CALL(symlink("/proc/self/fd", "dev/fd"));
}

// Recursively creates the file or directory specified in "path" and its parent
// directories.
static int CreateTarget(const char *path, bool is_directory) {
  static const char* ROOT_DIR = ".";

  if (path == NULL) {
    errno = EINVAL;
    return -1;
  }

  if (strlen(path) == 0) {
    path = ROOT_DIR;
  }

  struct stat sb;
  // If the path already exists...
  if (stat(path, &sb) == 0) {
    if (is_directory && S_ISDIR(sb.st_mode)) {
      // and it's a directory and supposed to be a directory, we're done here.
      return 0;
    } else if (!is_directory && S_ISREG(sb.st_mode)) {
      // and it's a regular file and supposed to be one, we're done here.
      return 0;
    } else {
      // otherwise something is really wrong.
      errno = is_directory ? ENOTDIR : EEXIST;
      return -1;
    }
  } else {
    // If stat failed because of any error other than "the path does not exist",
    // this is an error.
    if (errno != ENOENT) {
      return -1;
    }
  }

  // Create the parent directory.
  CHECK_CALL(CreateTarget(dirname(strdupa(path)), true));

  if (is_directory) {
    CHECK_CALL_MSG(mkdir(path, 0755), "cannot create %s", path);
  } else {
    CreateFile(path);
  }

  return 0;
}

static unsigned long GetMountFlags(const char *path)
{
  unsigned long ret = 0;

  FILE *mtab = setmntent("/proc/self/mounts", "r");
  if (mtab == NULL) {
    DIE("Cannot open /proc/self/mounts\n");
  }

  struct mntent *entry = NULL;
  while ((entry = getmntent(mtab)) != NULL) {
    if (strcmp(entry->mnt_dir, path) == 0) {
      break;
    }
  }

  if (entry != NULL) {
    if (hasmntopt(entry, "nodev")) {
      ret |= MS_NODEV;
    }
    if (hasmntopt(entry, "nosuid")) {
      ret |= MS_NOSUID;
    }
    if (hasmntopt(entry, "noexec")) {
      ret |= MS_NOEXEC;
    }
    PRINT_DEBUG("inferred mount options for %s: %lu\n", path, ret);
  } else {
    PRINT_DEBUG("could not find mount path: %s\n", path);
  }

  endmntent(mtab);
  return ret;
}

static void SetupDirectories(struct Options *opt, uid_t uid) {
  // Mount the sandbox and go there.
  CHECK_CALL(mount(opt->sandbox_root, opt->sandbox_root, NULL,
                   MS_BIND | MS_NOSUID, NULL));
  CHECK_CALL(chdir(opt->sandbox_root));

  // Setup /dev.
  SetupDevices();

  CHECK_CALL(mkdir("proc", 0755));
  CHECK_CALL(mount("/proc", "proc", NULL, MS_REC | MS_BIND, NULL));

  // Create needed directories.
  for (int i = 0; i < opt->num_create_dirs; i++) {
    PRINT_DEBUG("createdir: %s\n", opt->create_dirs[i]);
    CHECK_CALL(CreateTarget(opt->create_dirs[i] + 1, true));
  }

  // Mount all mounts.
  for (int i = 0; i < opt->num_mounts; i++) {
    struct stat sb;
    stat(opt->mount_sources[i], &sb);

    if (global_debug) {
      if (strcmp(opt->mount_sources[i], opt->mount_targets[i]) == 0) {
        // The file is mounted to the same path inside the sandbox, as outside
        // (e.g. /home/user -> <sandbox>/home/user), so we'll just show a
        // simplified version of the mount command.
        PRINT_DEBUG("mount: %s\n", opt->mount_sources[i]);
      } else {
        // The file is mounted to a custom location inside the sandbox.
        // Create a user-friendly string for the sandboxed path and show it.
        char *user_friendly_mount_target =
            malloc(strlen("<sandbox>") + strlen(opt->mount_targets[i]) + 1);
        strcpy(user_friendly_mount_target, "<sandbox>");
        strcat(user_friendly_mount_target, opt->mount_targets[i]);
        PRINT_DEBUG("mount: %s -> %s (%s)\n", opt->mount_sources[i],
                    user_friendly_mount_target,
                    opt->mount_rw[i] ? "rw" : "ro");
        free(user_friendly_mount_target);
      }
    }

    char *full_sandbox_path =
        malloc(strlen(opt->sandbox_root) + strlen(opt->mount_targets[i]) + 1);
    strcpy(full_sandbox_path, opt->sandbox_root);
    strcat(full_sandbox_path, opt->mount_targets[i]);
    CHECK_CALL(CreateTarget(full_sandbox_path, S_ISDIR(sb.st_mode)));
    CHECK_CALL_MSG(mount(opt->mount_sources[i], full_sandbox_path, NULL,
                         MS_REC | MS_BIND, NULL),
                   "cannot mount '%s' on '%s'", opt->mount_sources[i],
                   full_sandbox_path);
    if (!opt->mount_rw[i]) {
      unsigned long mnt_flags = GetMountFlags(full_sandbox_path);
      int ret = mount(opt->mount_sources[i], full_sandbox_path, NULL,
                      mnt_flags | MS_REC | MS_BIND | MS_REMOUNT | MS_RDONLY,
                      NULL);
      if (ret == -1) {
        fprintf(stderr, "warning: remounting %s read only failed: %s\n",
                full_sandbox_path, strerror(errno));
      }
    }
  }

  // Make sure the home directory exists, too. First try to get path from passwd
  // of sandbox. If this fails fall back to $HOME.
  char *homedir = NULL;
  FILE *passwd = fopen("etc/passwd", "r");
  if (passwd != NULL) {
    struct passwd *entry;
    do {
      entry = fgetpwent(passwd);
    } while (entry != NULL && entry->pw_uid != uid);
    fclose(passwd);

    if (entry == NULL) {
      homedir = getenv("HOME");
    } else {
      homedir = entry->pw_dir;
    }
  } else {
    PRINT_DEBUG("/etc/passwd not found/readable in sandbox! Falling back to $HOME\n");
    homedir = getenv("HOME");
  }

  if (homedir != NULL) {
    if (homedir[0] != '/') {
      DIE("Home directory must be an absolute path, but is %s\n", homedir);
    }
    PRINT_DEBUG("createdir: %s\n", homedir);
    CHECK_CALL(CreateTarget(homedir + 1, true));

    // Set $HOME to same path.
    CHECK_CALL(setenv("HOME", homedir, 1));
  }
}

// Write the file "filename" using a format string specified by "fmt". Returns
// -1 on failure.
static int WriteFile(const char *filename, const char *fmt, ...) {
  int r;
  va_list ap;
  FILE *stream = fopen(filename, "w");
  if (stream == NULL) {
    return -1;
  }
  va_start(ap, fmt);
  r = vfprintf(stream, fmt, ap);
  va_end(ap);
  if (r >= 0) {
    r = fclose(stream);
  }
  return r;
}

static void SetupUserNamespace(uid_t uid, uid_t gid, uid_t new_uid, uid_t new_gid) {
  // Disable needs for CAP_SETGID
  int r = WriteFile("/proc/self/setgroups", "deny");
  if (r < 0 && errno != ENOENT) {
    // Writing to /proc/self/setgroups might fail on earlier
    // version of linux because setgroups does not exist, ignore.
    perror("WriteFile(\"/proc/self/setgroups\", \"deny\")");
    exit(EXIT_FAILURE);
  }

  // Set group and user mapping from outer namespace to inner:
  // No changes in the parent, be nobody in the child.
  //
  // We can't be root in the child, because some code may assume that running as
  // root grants it certain capabilities that it doesn't in fact have. It's
  // safer to let the child think that it is just a normal user.
  CHECK_CALL(WriteFile("/proc/self/uid_map", "%d %d 1\n", new_uid, uid));
  CHECK_CALL(WriteFile("/proc/self/gid_map", "%d %d 1\n", new_gid, gid));

  CHECK_CALL(setresuid(new_uid, new_uid, new_uid));
  CHECK_CALL(setresgid(new_gid, new_gid, new_gid));
}

static void ChangeRoot(struct Options *opt) {
  // move the real root to old_root, then detach it
  char old_root[16] = "old-root-XXXXXX";
  if (mkdtemp(old_root) == NULL) {
    perror("mkdtemp");
    DIE("mkdtemp returned NULL\n");
  }

  // pivot_root has no wrapper in libc, so we need syscall()
  CHECK_CALL(syscall(SYS_pivot_root, ".", old_root));
  CHECK_CALL(chroot("."));
  CHECK_CALL(umount2(old_root, MNT_DETACH));
  CHECK_CALL(rmdir(old_root));

  if (opt->working_dir != NULL) {
    CHECK_CALL(chdir(opt->working_dir));
    CHECK_CALL(setenv("PWD", opt->working_dir, 1));
  }
}

// Run the command specified by the argv array and kill it after timeout
// seconds.
static void ExecCommand(char *const *argv) {
  for (int i = 0; argv[i] != NULL; i++) {
    PRINT_DEBUG("arg: %s\n", argv[i]);
  }

  // Force umask to include read and execute for everyone, to make
  // output permissions predictable.
  umask(022);

  // Does not return unless something went wrong.
  CHECK_CALL(execvp(argv[0], argv));
}

int main(int argc, char *const argv[]) {
  struct Options opt;
  memset(&opt, 0, sizeof(opt));
  opt.uid = kNobodyUid;
  opt.gid = kNobodyGid;
  opt.mount_sources = calloc(argc, sizeof(char *));
  opt.mount_targets = calloc(argc, sizeof(char *));
  opt.mount_rw = calloc(argc, sizeof(bool));
  opt.mount_map_sizes = argc;
  opt.create_dirs = calloc(argc, sizeof(char *));

  ParseCommandLine(argc, argv, &opt);
  if (opt.args == NULL) {
    Usage(argc, argv, "No command specified.");
  }
  if (opt.sandbox_root == NULL) {
    Usage(argc, argv, "Sandbox root (-S) must be specified");
  }

  uid_t uid = SwitchToEuid();
  uid_t gid = SwitchToEgid();

  RedirectStdout(opt.stdout_path);
  RedirectStderr(opt.stderr_path);

  PRINT_DEBUG("sandbox root is %s\n", opt.sandbox_root);
  PRINT_DEBUG("working dir is %s\n",
              (opt.working_dir != NULL) ? opt.working_dir : "/ (default)");

  CreateNamespaces(opt.create_netns);
  if (opt.create_netns) {
    // Enable the loopback interface because some application may want
    // to use it.
    BringupInterface("lo");
  }

  // Make our mount namespace private, so that further mounts do not affect the
  // outside environment.
  CHECK_CALL(mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL));

  SetupDirectories(&opt, opt.uid);
  SetupUserNamespace(uid, gid, opt.uid, opt.gid);
  if (opt.host_name) {
    CHECK_CALL(sethostname(opt.host_name, strlen(opt.host_name)));
  }
  ChangeRoot(&opt);

  ExecCommand(opt.args);

  // should not be reached but just in case...
  return 1;
}
