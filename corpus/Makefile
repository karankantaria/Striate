# Ground-truth corpus. All logic lives in build.py (cross-platform);
# this Makefile is the conventional entry point on hosts that have make.
# On Windows without make:  python corpus\build.py

PYTHON ?= python

.PHONY: all force clean

all:
	$(PYTHON) build.py

force:
	$(PYTHON) build.py --force

clean:
	$(RM) -r out
