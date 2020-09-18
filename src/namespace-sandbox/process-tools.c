// Copyright 2015 The Bazel Authors. All rights reserved.
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

#include <unistd.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <errno.h>
#include <signal.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>

#include "process-tools.h"

int SwitchToEuid() {
  int uid = getuid();
  int euid = geteuid();
  if (uid != euid) {
    CHECK_CALL(setreuid(euid, euid));
  }
  return euid;
}

int SwitchToEgid() {
  int gid = getgid();
  int egid = getegid();
  if (gid != egid) {
    CHECK_CALL(setregid(egid, egid));
  }
  return egid;
}

void Redirect(const char *target_path, int fd, const char *name) {
  if (target_path != NULL && strcmp(target_path, "-") != 0) {
    int fd_out;
    const int flags = O_WRONLY | O_CREAT | O_TRUNC | O_APPEND;
    CHECK_CALL(fd_out = open(target_path, flags, 0666));
    CHECK_CALL(dup2(fd_out, fd));
    CHECK_CALL(close(fd_out));
  }
}

void RedirectStdout(const char *stdout_path) {
  Redirect(stdout_path, STDOUT_FILENO, "stdout");
}

void RedirectStderr(const char *stderr_path) {
  Redirect(stderr_path, STDERR_FILENO, "stderr");
}
