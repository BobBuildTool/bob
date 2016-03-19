#!/usr/bin/gawk -f

@load "inplace"

function usage()
{
   print "NAME"
   print "       cleanup-recipes.awk"
   print ""
   print "SYNOPSIS"
   print "       gawk -i inplace -f cleanup-recipes.awk $(find recipes classes -type f -name '*.yaml')"
   print ""
   print "ARGUMENTS"
   print "       -i inplace"
   print "              enable GAWK inplace editting"
   print ""
   print "DESCRIPTION"
   print "       sort YAML items and remove trailing whitespaces in BOB recipes"
}

BEGIN {
   assert(ARGV[0] == "gawk")
   assert(ARGC > 1)
   count_files = ARGC - 1
}

BEGINFILE {
   EOF = 0
   while ((getline line < FILENAME) > 0)
   {
      EOF++
      if (line && line !~ /^[[:space:]]+?$/)
      {
         blank = EOF
      }
   }
   close(FILENAME)
   delete item
   delete block
}

# match single dash
match($0, /^([[:space:]]+[-]|[[:space:]]+[-][[:space:]]+.*)$/) {
   s1 = RLENGTH - 1
   add_item()
   delete item
   item[FNR] = $0
   check_EOF()
   next
}

# match key value pairs after single dash
s1 && match($0, /^[[:space:]]+[^[:space:]]+[:].*/) {
   s2 = RLENGTH
   if (s1 <= s2)
   {
      item[FNR] = $0
      check_EOF()
      next
   }
}

# end of YAML block
s1 > s2 || match($0, /^[[:graph:]]+/) || match($0, /^[[:space:]]+?/) || FNR == blank{
   s1 = 0
   s2 = 0
   add_item()
   delete item
   print_block()
}

FNR <= blank {
   gsub(/[[:space:]]+$/, "")
   print $0
}

END {
   if (_assert_exit)
   {
      exit (_assert_exit)
   }

   printf("\x1B[36mSUMMARY:\x1B[0m %i files processed\n", count_files)
}

# this function is needed because inplace editting
# is not allowed in ENDFILE section
function check_EOF()
{
   if (FNR == blank)
   {
      add_item()
      delete item
      print_block()
   }
}

function add_item()
{
   name = ""
   PROCINFO["sorted_in"] = "@ind_num_asc"
   for (i in item)
   {
      if (!name)
      {
         # this case is one item
         name = item[i]
         sub("-", "", name)
      }
      if (item[i] ~ "name:")
      {
         name = item[i]
         sub("name:", "", name)
      }
      if (item[i] ~ "url:")
      {
         name = item[i]
         sub("url:", "", name)
         gsub(/.*[^\/][\/]/, "", name)
         sub(/[.][^.]+$/, "", name)

         gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
         if (name in urls)
         {
            print_warn("warning: duplicate URL found", name)
         }
         urls[name]
      }
   }

   gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)

   PROCINFO["sorted_in"] = "@ind_num_asc"
   for (i in item)
   {
      block[name][i] = item[i]
   }
}

function print_block()
{
   PROCINFO["sorted_in"] = "sort_by_name"
   for (i in block)
   {
      PROCINFO["sorted_in"] = "@ind_num_asc"
      for (j in block[i])
      {
         v = block[i][j]
         gsub(/[[:space:]]+$/, "", v)
         print v
      }
   }
   delete block
}

# sort by name, items with "forward:" at first
function sort_by_name(i1, v1, i2, v2, n1, n2)
{
   if (isarray(v1))
   {
      for (k in v1) { n1 = v1[k] ~ /forward[:]/ ? 1 : n1 }
   }
   if (isarray(v2))
   {
      for (k in v2) { n2 = v2[k] ~ /forward[:]/ ? 1 : n2 }
   }
   if (n1 != n2)
   {
      return (n1 > n2 ? -1 : 1)
   }
   if (i1 == i2)
   {
      return 0
   }
   return (i1 < i2 ? -1 : 1)
}

function print_warn(msg, recipe)
{
   printf("\033[33mWARNING:\x1B[0m %s (%s)\n", msg, recipe) > "/dev/stderr"
   count_warn++;
}

function assert(condition, string)
{
   if (! condition)
   {
      usage() > "/dev/stderr"
      _assert_exit = 1
      exit (_assert_exit)
   }
}
