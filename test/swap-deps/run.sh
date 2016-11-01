# In develop build mode the directories are not 100% stable. If the dependency
# tree is changed the directories of the direct dependencies of a build step
# might change. This must lead to a clean build because many tools (e.g. cmake)
# assume stable input directories on incremental builds.

# Check environment
if [[ "$(type -t run_bob)" != function ]] ; then
  echo "Please run me via run-tests.sh" >&2
  exit 1
fi

# build in first variant
run_bob dev root -DUNUSED=0
V1="$(run_bob query-path --develop -DUNUSED=0 root/variant-a/dep)"

# search for variant where dependency directories swap
i=1
while [[ $i -lt 100 ]] ; do
	V2="$(run_bob query-path --develop -DUNUSED=$i root/variant-a/dep)"
	[ "$V1" = "$V2" ] || break
	: $(( i++ ))
done

if [ "$V1" = "$V2" ] ; then
	echo "Could not find case where directories are swapped"
	exit 1
fi

# build second variant incrementally
run_bob dev root -DUNUSED=$i
