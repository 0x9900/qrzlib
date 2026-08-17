#
# vim:ft=make
# fred, 2026-05-28 21:43
#
#
## Makefile for adif_parser Python module

.PHONY: help  clean pre-commit pylint build

help:
	@echo ""
	@echo "Use the following commands:"
	@echo "---------------------------"
	@echo "make all"
	@echo "make clean"
	@echo "make pre-commit"
	@echo "make pylint"
	@echo "make build"

all: pre-commit pylint mypy

# Clean: Remove build artifacts and cache
clean:
	rm -rf build/ dist/ *.egg-info/ __pycache__/

# Pre-commit: Run pre-commit hooks
pre-commit:
	pre-commit run --all-files

# Pylint: Run code linting
pylint:
	-pylint qrzlib

mypy:
	-mypy qrzlib

# Build: Build the Python package
build: clean all
	python -m build

# Install the development dependencies.
dev:
	pip install ".[dev]"

dep:
	pip install --only-deps .
