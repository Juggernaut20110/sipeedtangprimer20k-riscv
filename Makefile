PROFILE ?= minimal

.PHONY: setup doctor build load run compare test clean

setup:
	python3 scripts/setup.py

doctor:
	python3 scripts/doctor.py

build:
	python3 scripts/project.py build $(PROFILE)

load:
	python3 scripts/project.py load $(PROFILE)

run:
	python3 scripts/project.py run $(PROFILE) $(PORT)

compare:
	python3 scripts/project.py compare

test:
	python3 scripts/project.py test

clean:
	rm -rf build
