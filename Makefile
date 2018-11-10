# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

DESTDIR?=/usr/local

DIR=src/namespace-sandbox
SOURCE=namespace-sandbox.c network-tools.c process-tools.c
HEADERS=network-tools.h process-tools.h

# check if we can build manpages
SPHINX := $(shell command -v sphinx-build 2>/dev/null)

.PHONY: all install pym check doc

all: bin/namespace-sandbox check pym doc

bin/namespace-sandbox: $(patsubst %,$(DIR)/%,$(SOURCE) $(HEADERS))
	@gcc -o $@ -std=c99 $^ -lm

pym:
	@python3 -m compileall pym

install: all
	@mkdir -p $(DESTDIR)/bin $(DESTDIR)/lib/bob/bin
	@cp bin/namespace-sandbox $(DESTDIR)/lib/bob/bin
	@cp bin/namespace-sandbox $(DESTDIR)/bin/bob-namespace-sandbox
	@cp -r bob bob-audit-engine bob-hash-engine bob-hash-tree contrib pym $(DESTDIR)/lib/bob
	@ln -sf ../lib/bob/bob $(DESTDIR)/bin
	@ln -sf ../lib/bob/bob-audit-engine $(DESTDIR)/bin
	@ln -sf ../lib/bob/bob-hash-engine $(DESTDIR)/bin
	@if [ -d $(DESTDIR)/share/bash-completion ] ; then \
		ln -s $(DESTDIR)/lib/bob/contrib/bash-completion $(DESTDIR)/share/bash-completion/bob ; \
	fi
	@if [ -d .git ] ; then \
		git describe --tags --dirty > $(DESTDIR)/lib/bob/version 2> /dev/null \
		|| rm -rf $(DESTDIR)/lib/bob/version ; \
	else \
		rm -rf $(DESTDIR)/lib/bob/version ; \
	fi
ifdef SPHINX
	@for num in 1 7 ; do \
		mkdir -p $(DESTDIR)/share/man/man$$num ; \
		cp doc/_build/man/*.$$num $(DESTDIR)/share/man/man$$num/ ; \
	done
endif

check:
	@python3 -c 'import sys; sys.exit(0 if sys.hexversion >= 0x03050000 else 1)' || { echo "Pyton >= 3.5.0 is required!"; exit 1 ; }
	@python3 -c 'import schema' || { echo "Module 'schema' missing. Please install: 'pip3 install --user schema'..." ; exit 1 ; }
	@python3 -c 'import yaml' || { echo "Module 'yaml' missing. Please install: 'pip3 install --user PyYAML'..." ; exit 1 ; }

doc:
ifdef SPHINX
	@make -C doc man
else
	$(warning "sphinx-build is not available. Manpages will not be built!")
endif
