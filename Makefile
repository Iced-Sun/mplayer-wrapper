SHELL := /bin/bash

VPATH=mplayer
HEADER=\#!/usr/bin/env python
SOURCES := $(wildcard mplayer/*.py)

all : ${SOURCES}
	zip -j - ${SOURCES} | cat <(echo '${HEADER}') - > mplayer.pyz && chmod +x mplayer.pyz

.PHONY : clean
clean :
	-rm mplayer/*.pyc
