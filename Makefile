.PHONY: setup doctor build load run compare test benchmark-build benchmark-run benchmark-report clean

setup:
	python3 scripts/setup.py

doctor:
	python3 scripts/doctor.py

build:
	python3 scripts/project.py build $(or $(PROFILE),minimal)

load:
	python3 scripts/project.py load $(or $(PROFILE),minimal)

run:
	python3 scripts/project.py run $(or $(PROFILE),minimal) $(PORT)

compare:
	python3 scripts/project.py compare

test:
	python3 scripts/project.py test

benchmark-build:
	python3 scripts/project.py benchmark-build

benchmark-run:
	python3 scripts/project.py benchmark-run $(or $(PROFILE),standard) $(PORT)

benchmark-report:
	python3 scripts/project.py benchmark-report

clean:
	rm -rf build
